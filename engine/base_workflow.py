import math
import numpy as np
from tqdm import tqdm 
from abc import ABCMeta, abstractmethod

from utils.util import pad_and_reflect, apply_binary_mask, save_tif, check_downsample_division
from data.data_2D_manipulation import crop_data_with_overlap, merge_data_with_overlap
from data.data_3D_manipulation import crop_3D_data_with_overlap, merge_3D_data_with_overlap
from data.post_processing.post_processing import ensemble8_2d_predictions, ensemble16_3d_predictions
from engine.metrics import jaccard_index_numpy, voc_calculation
from data.post_processing import apply_post_processing

class Base_Workflow(metaclass=ABCMeta):
    def __init__(self, cfg, model, post_processing=False):
        self.cfg = cfg
        self.model = model
        self.post_processing = post_processing

        self.all_pred = []
        self.all_gt = []

        self.stats = {}

        # Per crop
        self.stats['loss_per_crop'] = 0
        self.stats['iou_per_crop'] = 0
        self.stats['patch_counter'] = 0

        # Merging the image
        self.stats['iou_per_image'] = 0
        self.stats['ov_iou_per_image'] = 0
        
        # Full image
        self.stats['loss'] = 0
        self.stats['iou'] = 0
        self.stats['ov_iou'] = 0

        # Post processing
        self.stats['iou_post'] = 0
        self.stats['ov_iou_post'] = 0
        

    def process_sample(self, X, Y, filenames): 
        #################
        ### PER PATCH ###
        #################
        if self.cfg.TEST.STATS.PER_PATCH:
            _X = X.copy()
            _Y = Y.copy() if self.cfg.DATA.TEST.LOAD_GT else None
            # Reflect data to complete the needed shape  
            if self.cfg.DATA.REFLECT_TO_COMPLETE_SHAPE:
                print()
                reflected_orig_shape = _X.shape
                _X = np.expand_dims(pad_and_reflect(_X[0], self.cfg.DATA.PATCH_SIZE, verbose=self.cfg.TEST.VERBOSE),0)
                if self.cfg.DATA.TEST.LOAD_GT:
                    _Y = np.expand_dims(pad_and_reflect(_Y[0], self.cfg.DATA.PATCH_SIZE, verbose=self.cfg.TEST.VERBOSE),0)

            original_data_shape = _X.shape 
           
            # Crop if necessary
            if self.cfg.PROBLEM.NDIM == '2D':
                t_patch_size = self.cfg.DATA.PATCH_SIZE
            else:
                t_patch_size = tuple(self.cfg.DATA.PATCH_SIZE[i] for i in [2, 1, 0, 3])
            if _X.shape[1:] != t_patch_size:
                if self.cfg.PROBLEM.NDIM == '2D':
                    obj = crop_data_with_overlap(_X, self.cfg.DATA.PATCH_SIZE, data_mask=_Y,
                        overlap=self.cfg.DATA.TEST.OVERLAP, padding=self.cfg.DATA.TEST.PADDING,
                        verbose=self.cfg.TEST.VERBOSE)
                    if self.cfg.DATA.TEST.LOAD_GT:
                        _X, _Y = obj
                    else:
                        _X = obj
                else:
                    if self.cfg.DATA.TEST.LOAD_GT: _Y = _Y[0]
                    obj = crop_3D_data_with_overlap(_X[0], self.cfg.DATA.PATCH_SIZE, data_mask=_Y,
                        overlap=self.cfg.DATA.TEST.OVERLAP, padding=self.cfg.DATA.TEST.PADDING,
                        verbose=self.cfg.TEST.VERBOSE, median_padding=self.cfg.DATA.TEST.MEDIAN_PADDING)
                    if self.cfg.DATA.TEST.LOAD_GT:
                        _X, _Y = obj
                    else:
                        _X = obj

            # Evaluate each patch
            if self.cfg.DATA.TEST.LOAD_GT and self.cfg.TEST.EVALUATE:
                l = int(math.ceil(_X.shape[0]/self.cfg.TRAIN.BATCH_SIZE))
                for k in tqdm(range(l), leave=False):
                    top = (k+1)*self.cfg.TRAIN.BATCH_SIZE if (k+1)*self.cfg.TRAIN.BATCH_SIZE < _X.shape[0] else _X.shape[0]
                    score = self.model.evaluate(
                        _X[k*self.cfg.TRAIN.BATCH_SIZE:top], _Y[k*self.cfg.TRAIN.BATCH_SIZE:top], verbose=0)
                    self.stats['loss_per_crop'] += score[0]
                    self.stats['iou_per_crop'] += score[1]
            self.stats['patch_counter'] += _X.shape[0]

            # Predict each patch
            pred = []
            if self.cfg.TEST.AUGMENTATION:
                for k in tqdm(range(_X.shape[0]), leave=False):
                    if self.cfg.PROBLEM.NDIM == '2D':
                        p = ensemble8_2d_predictions(_X[k], n_classes=self.cfg.MODEL.N_CLASSES,
                                pred_func=(lambda img_batch_subdiv: self.model.predict(img_batch_subdiv)))
                    else:
                        p = ensemble16_3d_predictions(_X[k], batch_size_value=self.cfg.TRAIN.BATCH_SIZE,
                                pred_func=(lambda img_batch_subdiv: self.model.predict(img_batch_subdiv)))
                    pred.append(np.expand_dims(p, 0))
            else:
                l = int(math.ceil(_X.shape[0]/self.cfg.TRAIN.BATCH_SIZE))
                for k in tqdm(range(l), leave=False):
                    top = (k+1)*self.cfg.TRAIN.BATCH_SIZE if (k+1)*self.cfg.TRAIN.BATCH_SIZE < _X.shape[0] else _X.shape[0]
                    p = self.model.predict(_X[k*self.cfg.TRAIN.BATCH_SIZE:top], verbose=0)
                    pred.append(p)

            # Reconstruct the predictions
            pred = np.concatenate(pred)
            if original_data_shape[1:] != t_patch_size:
                if self.cfg.PROBLEM.NDIM == '3D': original_data_shape = original_data_shape[1:]
                f_name = merge_data_with_overlap if self.cfg.PROBLEM.NDIM == '2D' else merge_3D_data_with_overlap
                obj = f_name(pred, original_data_shape[:-1]+(pred.shape[-1],), data_mask=_Y,
                                padding=self.cfg.DATA.TEST.PADDING, overlap=self.cfg.DATA.TEST.OVERLAP,
                                verbose=self.cfg.TEST.VERBOSE)
                if self.cfg.DATA.TEST.LOAD_GT:
                    pred, _Y = obj
                else:
                    pred = obj
            else:
                pred = pred[0]

            if self.cfg.DATA.REFLECT_TO_COMPLETE_SHAPE and self.cfg.PROBLEM.NDIM == '3D':
                pred = pred[-reflected_orig_shape[1]:,-reflected_orig_shape[2]:,-reflected_orig_shape[3]:]
                if _Y is not None:
                    _Y = _Y[-reflected_orig_shape[1]:,-reflected_orig_shape[2]:,-reflected_orig_shape[3]:]

            # Argmax if needed
            if self.cfg.MODEL.N_CLASSES > 1 and self.cfg.DATA.TEST.ARGMAX_TO_OUTPUT:
                pred = np.expand_dims(np.argmax(pred,-1), -1)
                if self.cfg.DATA.TEST.LOAD_GT: _Y = np.expand_dims(np.argmax(_Y,-1), -1)

            # Apply mask
            if self.cfg.TEST.APPLY_MASK:
                pred = apply_binary_mask(pred, self.cfg.DATA.TEST.BINARY_MASKS)

            # Save image
            if self.cfg.PATHS.RESULT_DIR.PER_IMAGE != "":
                save_tif(np.expand_dims(pred,0), self.cfg.PATHS.RESULT_DIR.PER_IMAGE, filenames, verbose=self.cfg.TEST.VERBOSE)


            #####################
            ### MERGE PATCHES ###
            #####################
            if self.cfg.TEST.STATS.MERGE_PATCHES:
                if self.cfg.DATA.TEST.LOAD_GT and self.cfg.DATA.CHANNELS != "Dv2":
                    _Y = _Y[0]
                    if self.cfg.LOSS.TYPE != 'MASKED_BCE':
                        _iou_per_image = jaccard_index_numpy((_Y>0.5).astype(np.uint8), (pred>0.5).astype(np.uint8))
                        _ov_iou_per_image = voc_calculation((_Y>0.5).astype(np.uint8), (pred>0.5).astype(np.uint8),
                                                        _iou_per_image)
                    else:
                        exclusion_mask = _Y < 2
                        bin_Y = _Y * exclusion_mask.astype( float )
                        _iou_per_image = jaccard_index_numpy((bin_Y>0.5).astype(np.uint8), (pred>0.5).astype(np.uint8))
                        _ov_iou_per_image = voc_calculation((bin_Y>0.5).astype(np.uint8), (pred>0.5).astype(np.uint8),
                                                        _iou_per_image)
                    self.stats['iou_per_image'] += _iou_per_image
                    self.stats['ov_iou_per_image'] += _ov_iou_per_image

                ############################
                ### POST-PROCESSING (3D) ###
                ############################
                if self.post_processing and self.cfg.PROBLEM.NDIM == '3D':
                    _iou_post, _ov_iou_post = apply_post_processing(self.cfg, pred, _Y)
                    self.stats['iou_post'] += _iou_post
                    self.stats['ov_iou_post'] += _ov_iou_post
                    if pred.ndim == 4 and self.cfg.PROBLEM.NDIM == '3D':
                        save_tif(np.expand_dims(pred,0), self.cfg.PATHS.RESULT_DIR.PER_IMAGE_POST_PROCESSING,
                                    filenames, verbose=self.cfg.TEST.VERBOSE)
                    else:
                        save_tif(pred, self.cfg.PATHS.RESULT_DIR.PER_IMAGE_POST_PROCESSING, filenames,
                                    verbose=self.cfg.TEST.VERBOSE)

            self.after_merge_patches(pred, _Y, filenames)


        ##################
        ### FULL IMAGE ###
        ##################
        if self.cfg.TEST.STATS.FULL_IMG and self.cfg.PROBLEM.NDIM == '2D' :
            X, o_test_shape = check_downsample_division(X, len(self.cfg.MODEL.FEATURE_MAPS)-1)
            if self.cfg.DATA.TEST.LOAD_GT:
                Y, _ = check_downsample_division(Y, len(self.cfg.MODEL.FEATURE_MAPS)-1)

            # Evaluate each img
            if self.cfg.DATA.TEST.LOAD_GT:
                score = self.model.evaluate(X, Y, verbose=0)
                self.stats['loss'] += score[0]

            # Make the prediction
            if self.cfg.TEST.AUGMENTATION:
                pred = ensemble8_2d_predictions(
                    X[0], pred_func=(lambda img_batch_subdiv: self.model.predict(img_batch_subdiv)),
                    n_classes=self.cfg.MODEL.N_CLASSES)
                pred = np.expand_dims(pred, 0)
            else:
                pred = self.model.predict(X, verbose=0)

            # Recover original shape if padded with check_downsample_division
            pred = pred[:,:o_test_shape[1],:o_test_shape[2]]
            if self.cfg.DATA.TEST.LOAD_GT: Y = Y[:,:o_test_shape[1],:o_test_shape[2]]

            # Save image
            if pred.ndim == 4 and self.cfg.PROBLEM.NDIM == '3D':
                save_tif(np.expand_dims(pred,0), self.cfg.PATHS.RESULT_DIR.FULL_IMAGE, filenames,
                            verbose=self.cfg.TEST.VERBOSE)
            else:
                save_tif(pred, self.cfg.PATHS.RESULT_DIR.FULL_IMAGE, filenames, verbose=self.cfg.TEST.VERBOSE)

            # Argmax if needed
            if self.cfg.MODEL.N_CLASSES > 1 and self.cfg.DATA.TEST.ARGMAX_TO_OUTPUT:
                pred = np.expand_dims(np.argmax(pred,-1), -1)
                if self.cfg.DATA.TEST.LOAD_GT: Y = np.expand_dims(np.argmax(Y,-1), -1)

            if self.cfg.DATA.TEST.LOAD_GT:
                score[1] = jaccard_index_numpy((Y>0.5).astype(np.uint8), (pred>0.5).astype(np.uint8))
                self.stats['iou'] += score[1]
                self.stats['ov_iou'] += voc_calculation((Y>0.5).astype(np.uint8), (pred>0.5).astype(np.uint8), score[1])

            if self.cfg.TEST.STATS.FULL_IMG and self.cfg.PROBLEM.NDIM == '2D' and self.post_processing:
                self.all_pred.append(pred)
                if self.cfg.DATA.TEST.LOAD_GT: self.all_gt.append(Y)

            self.after_full_image(pred, Y, filenames)

    def get_stats(self, image_counter):
        # Per crop
        self.stats['loss_per_crop'] = self.stats['loss_per_crop'] / self.stats['patch_counter'] 
        self.stats['iou_per_crop'] = self.stats['iou_per_crop'] / self.stats['patch_counter'] 

        # Merge patches
        self.stats['iou_per_image'] = self.stats['iou_per_image'] / image_counter
        self.stats['ov_iou_per_image'] = self.stats['ov_iou_per_image'] / image_counter

        # Full image
        self.stats['iou'] = self.stats['iou'] / image_counter
        self.stats['loss'] = self.stats['loss'] / image_counter
        self.stats['ov_iou'] = self.stats['ov_iou'] / image_counter

        self.normalize_stats(image_counter)

    def normalize_stats(self, image_counter):
        if self.post_processing and self.cfg.PROBLEM.NDIM == '3D':
            self.stats['iou_post'] = self.stats['iou_post'] / image_counter
            self.stats['ov_iou_post'] = self.stats['ov_iou_post'] / image_counter

    def print_stats(self, image_counter):
        self.get_stats(image_counter)
        if self.cfg.DATA.TEST.LOAD_GT:
            if self.cfg.TEST.STATS.PER_PATCH:
                print("Loss (per patch): {}".format(self.stats['loss_per_crop']))
                print("Test Foreground IoU (per patch): {}".format(self.stats['iou_per_crop']))
                print(" ")
                if self.cfg.TEST.STATS.MERGE_PATCHES:
                    print("Test Foreground IoU (merge patches): {}".format(self.stats['iou_per_image']))
                    print("Test Overall IoU (merge patches): {}".format(self.stats['ov_iou_per_image']))
                    print(" ")
            if self.cfg.TEST.STATS.FULL_IMG:
                print("Loss (per image): {}".format(self.stats['loss']))
                print("Test Foreground IoU (per image): {}".format(self.stats['iou']))
                print("Test Overall IoU (per image): {}".format(self.stats['ov_iou']))
                print(" ")

    def print_post_processing_stats(self):
        if self.post_processing:
            print("Test Foreground IoU (post-processing): {}".format(self.stats['iou_post']))
            print("Test Overall IoU (post-processing): {}".format(self.stats['ov_iou_post']))
            print(" ")

    
    @abstractmethod
    def after_merge_patches(self, pred, Y, filenames):
        raise NotImplementedError

    @abstractmethod
    def after_full_image(self):
        raise NotImplementedError

    def after_all_images(self, Y):
        ############################
        ### POST-PROCESSING (2D) ###
        ############################
        if self.cfg.TEST.STATS.FULL_IMG and self.cfg.PROBLEM.NDIM == '2D' and self.post_processing:
            self.all_pred = np.concatenate(self.all_pred)
            if self.cfg.DATA.TEST.LOAD_GT:
                self.all_gt = np.concatenate(self.all_gt)
                self.stats['iou_post'], self.stats['ov_iou_post'] = apply_post_processing(self.cfg, self.all_pred, self.all_gt)
            else:
                self.stats['iou_post'], self.stats['ov_iou_post'] = 0, 0
            save_tif(self.all_pred, self.cfg.PATHS.RESULT_DIR.FULL_POST_PROCESSING, verbose=self.cfg.TEST.VERBOSE)
            del self.all_pred
