#!/usr/bin/env python
"""
Copyright 2018, Zixin Luo, HKUST.
Conduct pair-wise image matching.
"""

from __future__ import print_function

import os
import sys
import time

from threading import Thread
from queue import Queue

import cv2
import numpy as np
import tensorflow as tf

CURDIR = os.path.dirname(__file__)
sys.path.append(os.path.abspath(os.path.join(CURDIR, '..')))

from utils.tf import load_frozen_model
from utils.opencvhelper import SiftWrapper, MatcherWrapper

FLAGS = tf.app.flags.FLAGS

tf.app.flags.DEFINE_string('model_path', '../model/geodesc.pb',
                           """Path to evaluati3n model.""")
tf.app.flags.DEFINE_integer('batch_size', 512,
                            """Inference batch size.""")
tf.app.flags.DEFINE_integer('max_kpt_num', 8192,
                            """Maximum number of keypoints. Sampled by octave.""")
tf.app.flags.DEFINE_string('img1_path', '../img/test_img1.png',
                           """Path to the first image.""")
tf.app.flags.DEFINE_string('img2_path', '../img/test_img2.png',
                           """Path to the second image.""")
tf.app.flags.DEFINE_boolean('cf_sift', False,
                            """Compare with SIFT feature.""")


def extract_deep_features(sift_wrapper, sess, img_path, qtz=True):
    img = cv2.imread(img_path)
    gray_img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    # detect SIFT keypoints.
    start = time.time()
    _, cv_kpts = sift_wrapper.detect(gray_img)
    end = time.time()
    print('Time cost in keypoint detection', end - start)

    start = time.time()
    sift_wrapper.build_pyramid(gray_img)
    end = time.time()
    print('Time cost in scale space construction', end - start)

    start = time.time()
    all_patches = sift_wrapper.get_patches(cv_kpts)
    end = time.time()
    print('Time cost in patch cropping', end - start)

    num_patch = all_patches.shape[0]

    if num_patch % FLAGS.batch_size > 0:
        loop_num = int(np.floor(float(num_patch) / float(FLAGS.batch_size)))
    else:
        loop_num = int(num_patch / FLAGS.batch_size - 1)

    def _worker(patch_queue, sess, all_feat):
        """The worker thread."""
        while True:
            patch_data = patch_queue.get()
            if patch_data is None:
                return
            feat = sess.run("squeeze_1:0", feed_dict={"input:0": np.expand_dims(patch_data, -1)})
            all_feat.append(feat)
            patch_queue.task_done()

    all_feat = []
    patch_queue = Queue()
    worker_thread = Thread(target=_worker, args=(patch_queue, sess, all_feat))
    worker_thread.daemon = True
    worker_thread.start()

    start = time.time()

    # enqueue
    for i in range(loop_num + 1):
        if i < loop_num:
            patch_queue.put(all_patches[i * FLAGS.batch_size: (i + 1) * FLAGS.batch_size])
        else:
            patch_queue.put(all_patches[i * FLAGS.batch_size:])
    # poison pill
    patch_queue.put(None)
    # wait for extraction.
    worker_thread.join()

    end = time.time()
    print('Time cost in feature extraction', end - start)

    all_feat = np.concatenate(all_feat, axis=0)
    # quantize output features.
    all_feat = (all_feat * 128 + 128).astype(np.uint8) if qtz else all_feat
    return all_feat, cv_kpts, img


def main(argv=None):  # pylint: disable=unused-argument
    """Program entrance."""
    # create sift detector.
    sift_wrapper = SiftWrapper(n_sample=FLAGS.max_kpt_num)
    sift_wrapper.create()
    # create deep feature extractor.
    graph = load_frozen_model(FLAGS.model_path, print_nodes=False)
    sess = tf.Session(graph=graph)
    # extract deep feature from images.
    deep_feat1, cv_kpts1, img1 = extract_deep_features(
        sift_wrapper, sess, FLAGS.img1_path, qtz=False)
    deep_feat2, cv_kpts2, img2 = extract_deep_features(
        sift_wrapper, sess, FLAGS.img2_path, qtz=False)
    # match features by OpenCV brute-force matcher (CPU).
    matcher_wrapper = MatcherWrapper()
    # the ratio criterion is set to 0.89 for GeoDesc as described in the paper.
    deep_good_matches, deep_mask = matcher_wrapper.get_matches(
        deep_feat1, deep_feat2, cv_kpts1, cv_kpts2, ratio=0.89, cross_check=True, info='deep')

    deep_display = matcher_wrapper.draw_matches(
        img1, cv_kpts1, img2, cv_kpts2, deep_good_matches, deep_mask)
    # compare with SIFT.
    if FLAGS.cf_sift:
        sift_feat1 = sift_wrapper.compute(img1, cv_kpts1)
        sift_feat2 = sift_wrapper.compute(img2, cv_kpts2)
        sift_good_matches, sift_mask = matcher_wrapper.get_matches(
            sift_feat1, sift_feat2, cv_kpts1, cv_kpts2, ratio=0.80, cross_check=True, info='sift')
        sift_display = matcher_wrapper.draw_matches(
            img1, cv_kpts1, img2, cv_kpts2, sift_good_matches, sift_mask)
        display = np.concatenate((sift_display, deep_display), axis=0)
    else:
        display = deep_display

    cv2.imshow('display', display)
    cv2.waitKey()

    sess.close()


if __name__ == '__main__':
    tf.app.run()
