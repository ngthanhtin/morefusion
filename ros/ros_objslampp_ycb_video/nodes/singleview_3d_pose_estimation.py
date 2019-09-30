#!/usr/bin/env python

import json

import chainer
import numpy as np
import path
import imgviz

import objslampp
import objslampp.contrib.singleview_3d as contrib

import cv_bridge
import rospy
from topic_tools import LazyTransport
import message_filters
from sensor_msgs.msg import Image, CameraInfo
from visualization_msgs.msg import Marker, MarkerArray


class SingleViewPoseEstimation3D(LazyTransport):

    _models = objslampp.datasets.YCBVideoModels()

    def __init__(self):
        pretrained_model = '/home/wkentaro/objslampp/examples/ycb_video/singleview_3d/logs.20190920.all_class/20190927_101303.421589569/snapshot_model_best_add.npz'  # NOQA
        args_file = path.Path(pretrained_model).parent / 'args'

        with open(args_file) as f:
            args_data = json.load(f)

        self._model = contrib.models.Model(
            n_fg_class=len(args_data['class_names'][1:]),
            pretrained_resnet18=args_data['pretrained_resnet18'],
            with_occupancy=args_data['with_occupancy'],
            loss=args_data['loss'],
            loss_scale=args_data['loss_scale'],
        )
        chainer.serializers.load_npz(pretrained_model, self._model)
        self._model.to_gpu()

        super().__init__()
        self._pub_ins = self.advertise(
            '~output/label_ins_viz', Image, queue_size=1
        )
        self._pub_markers = self.advertise(
            '~output/markers', MarkerArray, queue_size=1
        )

    def subscribe(self):
        self._sub_cam = message_filters.Subscriber(
            '~input/camera_info', CameraInfo
        )
        self._sub_rgb = message_filters.Subscriber('~input/rgb', Image)
        self._sub_depth = message_filters.Subscriber('~input/depth', Image)
        self._sub_ins = message_filters.Subscriber('~input/label_ins', Image)
        self._sub_cls = message_filters.Subscriber('~input/label_cls', Image)
        self._subscribers = [
            self._sub_cam,
            self._sub_rgb,
            self._sub_depth,
            self._sub_ins,
            self._sub_cls,
        ]
        sync = message_filters.TimeSynchronizer(
            self._subscribers, queue_size=100
        )
        sync.registerCallback(self._callback)

    def unsubscribe(self):
        for sub in self._subscribers:
            sub.unregister()

    def _callback(self, cam_msg, rgb_msg, depth_msg, ins_msg, cls_msg):
        bridge = cv_bridge.CvBridge()
        rgb = bridge.imgmsg_to_cv2(rgb_msg, desired_encoding='rgb8')
        depth = bridge.imgmsg_to_cv2(depth_msg)
        if depth.dtype == np.uint16:
            depth = depth.astype(np.float32) / 1000
            depth[depth == 0] = np.nan
        assert depth.dtype == np.float32
        K = np.array(cam_msg.K).reshape(3, 3)
        pcd = objslampp.geometry.pointcloud_from_depth(
            depth, K[0, 0], K[1, 1], K[0, 2], K[1, 2]
        )
        ins = bridge.imgmsg_to_cv2(ins_msg)
        cls = bridge.imgmsg_to_cv2(cls_msg)

        ins_viz = imgviz.label2rgb(ins + 1, rgb)
        ins_viz_msg = bridge.cv2_to_imgmsg(ins_viz, encoding='rgb8')
        ins_viz_msg.header = rgb_msg.header
        self._pub_ins.publish(ins_viz_msg)

        instance_ids = np.unique(ins)
        instance_ids = instance_ids[instance_ids >= 0]

        examples = []
        for ins_id in instance_ids:
            mask = ins == ins_id
            if mask.sum() < 50:
                continue
            unique, counts = np.unique(cls[mask], return_counts=True)
            cls_id = unique[np.argmax(counts)]
            bbox = objslampp.geometry.masks_to_bboxes([mask])[0]
            y1, x1, y2, x2 = bbox.round().astype(int)
            rgb_ins = rgb[y1:y2, x1:x2].copy()
            rgb_ins[~mask[y1:y2, x1:x2]] = 0
            rgb_ins = imgviz.centerize(rgb_ins, (256, 256), cval=0)
            pcd_ins = pcd[y1:y2, x1:x2].copy()
            pcd_ins[~mask[y1:y2, x1:x2]] = np.nan
            pcd_ins = imgviz.centerize(
                pcd_ins, (256, 256), cval=np.nan, interpolation='nearest'
            )
            examples.append(dict(
                class_id=cls_id,
                rgb=rgb_ins,
                pcd=pcd_ins,
            ))
        if not examples:
            return
        inputs = chainer.dataset.concat_examples(examples, device=0)

        with chainer.no_backprop_mode(), chainer.using_config('train', False):
            quaternion, translation, confidence = self._model.predict(**inputs)
        indices = confidence.array.argmax(axis=1)
        B = quaternion.shape[0]
        quaternion = quaternion[np.arange(B), indices]
        translation = translation[np.arange(B), indices]
        quaternion = chainer.cuda.to_cpu(quaternion.array)
        translation = chainer.cuda.to_cpu(translation.array)

        markers = MarkerArray()
        for i in range(B):
            cls_id = examples[i]['class_id']
            marker = Marker()
            marker.header = rgb_msg.header
            marker.ns = '/singleview_3d_pose_estimation'
            marker.id = i
            marker.lifetime.nsecs = 1
            marker.action = Marker.ADD
            marker.type = Marker.MESH_RESOURCE
            marker.pose.position.x = translation[i][0]
            marker.pose.position.y = translation[i][1]
            marker.pose.position.z = translation[i][2]
            marker.pose.orientation.x = quaternion[i][1]
            marker.pose.orientation.y = quaternion[i][2]
            marker.pose.orientation.z = quaternion[i][3]
            marker.pose.orientation.w = quaternion[i][0]
            marker.scale.x = 1
            marker.scale.y = 1
            marker.scale.z = 1
            cad_file = self._models.get_cad_file(cls_id)
            marker.mesh_resource = f'file://{cad_file}'
            marker.mesh_use_embedded_materials = True
            markers.markers.append(marker)
        self._pub_markers.publish(markers)


if __name__ == '__main__':
    rospy.init_node('singleview_3d_pose_estimation')
    SingleViewPoseEstimation3D()
    rospy.spin()
