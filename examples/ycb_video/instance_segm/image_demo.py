#!/usr/bin/env python

import argparse

import chainer
import chainercv
import imgviz
import numpy as np
import path
import termcolor

import objslampp


here = path.Path(__file__).abspath().parent


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    root_dir = path.Path(chainer.dataset.get_dataset_directory(
        'wkentaro/objslampp/ycb_video/lab_data/20190613/1560417263794359922',
        create_directory=False,
    ))
    default_image = root_dir / 'image.png'
    parser.add_argument(
        '--image',
        help='image file',
        default=default_image,
    )
    default_model = here / 'logs/20190518_071729/model_iter_best'
    parser.add_argument(
        '--model',
        help='model file',
        default=default_model,
    )
    args = parser.parse_args()

    args.image = path.Path(args.image)
    args.model = path.Path(args.model)

    rgb = imgviz.io.imread(args.image)

    # -------------------------------------------------------------------------

    mask_rcnn = chainercv.links.model.fpn.MaskRCNNFPNResNet50(
        n_fg_class=21,
        pretrained_model=args.model,
    )
    mask_rcnn.to_gpu()

    masks_batch, labels_batch, scores_batch = mask_rcnn.predict(
        [rgb.transpose(2, 0, 1).astype(np.float32)]
    )
    masks = masks_batch[0]
    labels = labels_batch[0]
    scores = scores_batch[0]

    # save detections
    detections_file = args.image.parent / 'detections.npz'
    np.savez_compressed(
        detections_file,
        masks=masks,
        labels=labels,
        scores=scores,
    )
    termcolor.cprint(f'==> Saved to {detections_file}', attrs={'bold': True})

    # visualize detections
    class_ids = labels + 1
    captions = [
        f'{c:02d}: {objslampp.datasets.ycb_video.class_names[c]}'
        for c in class_ids
    ]
    detections_viz = imgviz.instances2rgb(
        rgb,
        labels=class_ids,
        masks=masks,
        captions=captions,
        font_size=15,
        line_width=2,
    )

    detections_viz_file = args.image.parent / 'detections_viz.png'
    imgviz.io.imsave(detections_viz_file, detections_viz)
    termcolor.cprint(
        f'==> Saved to {detections_viz_file}', attrs={'bold': True}
    )

    imgviz.io.pyglet_imshow(detections_viz)
    imgviz.io.pyglet_run()


if __name__ == '__main__':
    main()
