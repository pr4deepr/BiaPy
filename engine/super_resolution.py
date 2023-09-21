import math
import torch
import numpy as np
from tqdm import tqdm
from torchmetrics.image import PeakSignalNoiseRatio

from data.data_2D_manipulation import crop_data_with_overlap, merge_data_with_overlap
from data.data_3D_manipulation import crop_3D_data_with_overlap, merge_3D_data_with_overlap
from data.post_processing.post_processing import ensemble8_2d_predictions, ensemble16_3d_predictions
from utils.util import save_tif
from utils.misc import to_pytorch_format, to_numpy_format
from engine.base_workflow import Base_Workflow
from engine.metrics import dfcan_loss
from data.pre_processing import denormalize, undo_norm_range01

class Super_resolution_Workflow(Base_Workflow):
    def __init__(self, cfg, job_identifier, device, rank, **kwargs):
        super(Super_resolution_Workflow, self).__init__(cfg, job_identifier, device, rank, **kwargs)
        self.stats['psnr_per_image'] = 0

        # From now on, no modification of the cfg will be allowed
        self.cfg.freeze()

        # Activations for each output channel:
        # channel number : 'activation'
        self.activations = {':': 'Linear'}

        # Workflow specific training variables
        self.mask_path = cfg.DATA.TRAIN.GT_PATH
        self.load_Y_val = True

    def define_metrics(self):
        self.metrics = [PeakSignalNoiseRatio()]
        self.metric_names = ["PSNR"]
        if self.cfg.MODEL.ARCHITECTURE == 'dfcan':
            print("Overriding 'LOSS.TYPE' to set it to DFCAN loss")
            self.loss = dfcan_loss(self.device)
        else:
            print("Overriding 'LOSS.TYPE' to set it to MAE")
            self.loss = torch.nn.L1Loss()

    def metric_calculation(self, output, targets, device, metric_logger=None):
        # Denormalization to calculate PSNR with original range values 
        output = output.to(torch.float32).detach().cpu()
        targets = targets.to(torch.float32).detach().cpu()
        if self.data_norm['type'] == 'div':
            if len([x for x in list(self.data_norm.keys()) if not 'reduced' in x]) > 0:
                output = torch.clip(output*255, 0, 255) 
                targets = torch.round(targets*255) 
            else:
                output = torch.clip(output*65535, 0, 65535)
                targets = torch.round(targets*65535) 
            output = torch.round(output)
        else:
            output = (x * self.data_norm['std']) + self.data_norm['mean']
            output = torch.round(output)                                                                 
            output = output+abs(torch.mint(output))

            targets = (x * self.data_norm['std']) + self.data_norm['mean']
            targets = torch.round(targets)                                                                 
            targets = targets+abs(torch.mint(targets))

        with torch.no_grad():
            train_psnr = self.metrics[0](output, targets)
            train_psnr = train_psnr.item() if not torch.isnan(train_psnr) else 0
            if metric_logger is not None:
                metric_logger.meters[self.metric_names[0]].update(train_psnr)
            else:
                return train_psnr

    def prepare_targets(self, targets, batch):
        targets = to_pytorch_format(targets, self.axis_order, self.device)
        if self.data_norm['type'] == 'div':
            if len([x for x in list(self.data_norm.keys()) if not 'reduced' in x]) > 0:
                targets = targets/255
            else:
                targets = targets/65535
        else:
            targets = normalize(targets, self.data_norm['mean'], self.data_norm['std'])
        return targets

    def process_sample(self, filenames, norm): 
        original_data_shape = (self._X.shape[0], self._X.shape[1]*self.cfg.PROBLEM.SUPER_RESOLUTION.UPSCALING,
                              self._X.shape[2]*self.cfg.PROBLEM.SUPER_RESOLUTION.UPSCALING, self._X.shape[3])

        # Crop if necessary
        if self._X.shape[1:-1] != self.cfg.DATA.PATCH_SIZE[:-1]:
            if self.cfg.PROBLEM.NDIM == '2D':
                self._X = crop_data_with_overlap(self._X, self.cfg.DATA.PATCH_SIZE, overlap=self.cfg.DATA.TEST.OVERLAP, 
                    padding=self.cfg.DATA.TEST.PADDING, verbose=self.cfg.TEST.VERBOSE)
            else:
                self._X = crop_3D_data_with_overlap(self._X[0], self.cfg.DATA.PATCH_SIZE, overlap=self.cfg.DATA.TEST.OVERLAP, 
                    padding=self.cfg.DATA.TEST.PADDING, verbose=self.cfg.TEST.VERBOSE)

        # Predict each patch
        pred = []
        if self.cfg.TEST.AUGMENTATION:
            for k in tqdm(range(self._X.shape[0]), leave=False):
                if self.cfg.PROBLEM.NDIM == '2D':
                    p = ensemble8_2d_predictions(self._X[k], n_classes=self.cfg.MODEL.N_CLASSES,
                            pred_func=(
                                lambda img_batch_subdiv: 
                                    to_numpy_format(
                                        self.apply_model_activations(
                                            self.model(to_pytorch_format(img_batch_subdiv, self.axis_order, self.device)),
                                            ), 
                                        self.axis_order_back
                                    )
                            )
                        )
                else:
                    p = ensemble16_3d_predictions(self._X[k], batch_size_value=self.cfg.TRAIN.BATCH_SIZE,
                            pred_func=(
                                lambda img_batch_subdiv: 
                                    to_numpy_format(
                                        self.apply_model_activations(
                                            self.model(to_pytorch_format(img_batch_subdiv, self.axis_order, self.device)),
                                            ), 
                                        self.axis_order_back
                                    )
                            )
                        )
                pred.append(np.expand_dims(p,0))
        else:
            self._X = to_pytorch_format(self._X, self.axis_order, self.device)
            l = int(math.ceil(self._X.shape[0]/self.cfg.TRAIN.BATCH_SIZE))
            for k in tqdm(range(l), leave=False):
                top = (k+1)*self.cfg.TRAIN.BATCH_SIZE if (k+1)*self.cfg.TRAIN.BATCH_SIZE < self._X.shape[0] else self._X.shape[0]
                with torch.cuda.amp.autocast():
                    p = self.model(self._X[k*self.cfg.TRAIN.BATCH_SIZE:top])
                    p = to_numpy_format(self.apply_model_activations(p), self.axis_order_back)
                pred.append(p)
        del self._X, p

        # Reconstruct the predictions
        pred = np.concatenate(pred)
        if original_data_shape[1:-1] != self.cfg.DATA.PATCH_SIZE[:-1]:
            if self.cfg.PROBLEM.NDIM == '3D': original_data_shape = original_data_shape[1:]
            f_name = merge_data_with_overlap if self.cfg.PROBLEM.NDIM == '2D' else merge_3D_data_with_overlap
            pad = tuple(p*self.cfg.PROBLEM.SUPER_RESOLUTION.UPSCALING for p in self.cfg.DATA.TEST.PADDING)
            ov = tuple(o*self.cfg.PROBLEM.SUPER_RESOLUTION.UPSCALING for o in self.cfg.DATA.TEST.OVERLAP) 
            pred = f_name(pred, original_data_shape[:-1]+(pred.shape[-1],), padding=pad, 
                overlap=ov, verbose=self.cfg.TEST.VERBOSE)
        else:
            pred = pred[0]

        # Undo normalization
        x_norm = norm[0]
        if x_norm['type'] == 'div':
            pred = undo_norm_range01(pred, x_norm)
        else:
            pred = denormalize(pred, x_norm['mean'], x_norm['std'])  
            
            if x_norm['orig_dtype'] not in [np.dtype('float64'), np.dtype('float32'), np.dtype('float16')]:
                pred = np.round(pred)
                minpred = np.min(pred)                                                                                                
                pred = pred+abs(minpred)

            pred = pred.astype(x_norm['orig_dtype'])

        # Save image
        if self.cfg.PATHS.RESULT_DIR.PER_IMAGE != "":
            save_tif(np.expand_dims(pred,0), self.cfg.PATHS.RESULT_DIR.PER_IMAGE, filenames, verbose=self.cfg.TEST.VERBOSE)
    
        # Calculate PSNR
        if self.cfg.DATA.TEST.LOAD_GT or self.cfg.DATA.TEST.USE_VAL_AS_TEST:
            psnr_per_image = self.metrics[0](torch.from_numpy(pred), torch.from_numpy(self._Y))
            self.stats['psnr_per_image'] += psnr_per_image

    def after_merge_patches(self, pred, filenames):
        pass

    def after_full_image(self, pred, filenames):
        pass

    def after_all_images(self):
        pass

    def normalize_stats(self, image_counter):
        self.stats['psnr_per_image'] = self.stats['psnr_per_image'] / image_counter

    def print_stats(self, image_counter):
        self.normalize_stats(image_counter)

        if self.cfg.DATA.TEST.LOAD_GT or self.cfg.DATA.TEST.USE_VAL_AS_TEST:
            print("Test PSNR (merge patches): {}".format(self.stats['psnr_per_image']))
            print(" ")


        