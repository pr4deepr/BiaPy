import os
import tensorflow as tf
from tensorflow.keras.callbacks import EarlyStopping

from utils.callbacks import ModelCheckpoint, TimeHistory
from engine.metrics import (jaccard_index, jaccard_index_softmax, IoU_instances,
                            instance_segmentation_loss, weighted_bce_dice_loss,
                            masked_bce_loss, masked_jaccard_index, PSNR, 
                            n2v_loss_mse)


def prepare_optimizer(cfg, model):
    """Select the optimizer, loss and metrics for the given model.

       Parameters
       ----------
       cfg : YACS CN object
           Configuration.

       model : Keras model
           Model to be compiled with the selected options.
    """

    # Select the optimizer
    if cfg.TRAIN.OPTIMIZER == "SGD":
        opt = tf.keras.optimizers.SGD(lr=cfg.TRAIN.LR, momentum=0.99, decay=0.0, nesterov=False)
    elif cfg.TRAIN.OPTIMIZER == "ADAM":
        opt = tf.keras.optimizers.Adam(lr=cfg.TRAIN.LR, beta_1=0.9, beta_2=0.999, epsilon=None, decay=0.0, amsgrad=False)

    # Compile the model
    metric_name = ''
    if cfg.PROBLEM.TYPE == "CLASSIFICATION":
        metric_name = "accuracy"
        model.compile(optimizer=opt, loss='categorical_crossentropy', metrics=[metric_name])
    elif cfg.PROBLEM.TYPE in ["SEMANTIC_SEG", 'DETECTION']:
        if cfg.LOSS.TYPE == "CE": 
            if cfg.MODEL.N_CLASSES == 1 or cfg.MODEL.N_CLASSES == 2: # Binary case
                fname = jaccard_index
                loss_name = 'binary_crossentropy'
                metric_name = "jaccard_index"
            else: # Multiclass
                # Use softmax jaccard if it is not going to be done in the last layer of the model
                if cfg.MODEL.LAST_ACTIVATION != 'softmax':
                    fname = jaccard_index_softmax  
                    loss_name = 'categorical_crossentropy'
                    metric_name = "jaccard_index_softmax"
                else:
                    fname = jaccard_index
                    metric_name = "jaccard_index"
                    loss_name = 'sparse_categorical_crossentropy'
            model.compile(optimizer=opt, loss=loss_name, metrics=[fname]) 
        elif cfg.LOSS.TYPE == "MASKED_BCE":
            metric_name = "masked_jaccard_index"
            model.compile(optimizer=opt, loss=masked_bce_loss, metrics=[masked_jaccard_index])
        elif cfg.LOSS.TYPE == "W_CE_DICE":
            model.compile(optimizer=opt, loss=weighted_bce_dice_loss(w_dice=0.66, w_bce=0.33), metrics=[jaccard_index])
            metric_name = "jaccard_index"
    elif cfg.PROBLEM.TYPE == "INSTANCE_SEG":
            if cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNELS == "Dv2":
                metric_name = "mse"
                model.compile(optimizer=opt, loss=instance_segmentation_loss(cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNEL_WEIGHTS, cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNELS),
                                metrics=[metric_name])
            else:   
                bin_channels = 2 if cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNELS in ["BCD", "BCDv2", "BC", ] else 1
                metric_name = "jaccard_index_instances"
                model.compile(optimizer=opt, loss=instance_segmentation_loss(cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNEL_WEIGHTS, cfg.PROBLEM.INSTANCE_SEG.DATA_CHANNELS),
                            metrics=[IoU_instances(binary_channels=bin_channels)])       
    elif cfg.PROBLEM.TYPE in ["SUPER_RESOLUTION", "SELF_SUPERVISED"]:
        print("Overriding 'LOSS.TYPE' to set it to MAE")
        model.compile(optimizer=opt, loss="mae", metrics=[PSNR])
        metric_name = "PSNR"
    elif cfg.PROBLEM.TYPE == "DENOISING":
        print("Overriding 'LOSS.TYPE' to set it to N2V loss (masked MSE)")
        model.compile(optimizer=opt, loss=n2v_loss_mse(), metrics=[n2v_loss_mse()])
        metric_name = "n2v_mse"
    return metric_name

def build_callbacks(cfg):
    """Create training and validation generators.

       Parameters
       ----------
       cfg : YACS CN object
           Configuration.

       Returns
       -------
       callbacks : List of callbacks
           All callbacks to be applied to a model.
    """

    callbacks = []

    # To measure the time
    time_callback = TimeHistory()
    callbacks.append(time_callback)

    # Stop early and restore the best model weights when finished the training
    earlystopper = EarlyStopping(monitor=cfg.TRAIN.EARLYSTOPPING_MONITOR, patience=cfg.TRAIN.PATIENCE, verbose=1,
                                 restore_best_weights=True)
    callbacks.append(earlystopper)

    # Save the best model into a h5 file in case one need again the weights learned
    os.makedirs(cfg.PATHS.CHECKPOINT, exist_ok=True)
    checkpointer = ModelCheckpoint(cfg.PATHS.CHECKPOINT_FILE, monitor=cfg.TRAIN.CHECKPOINT_MONITOR, verbose=1,
                                   save_best_only=True)
    callbacks.append(checkpointer)

    return callbacks
