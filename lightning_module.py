import math
import sys
sys.path.append('/home/aistudio/external-libraries')

import lightning as L
import torch
from torch.optim.lr_scheduler import LambdaLR

from models.aada import AadaNet
from models.vae import VaeNet


WINDOW_SIZE = 1024
WINDOW_STRIDE = 256   


class NilmNet(L.LightningModule):
    def __init__(self, net_name, config) -> None:
        super().__init__()
        self.config = config
        if net_name == 'aada':
            self.model = AadaNet(
                plates=config.plates, 
                midplates=config.midplates, 
                n_heads=config.n_heads, 
                dropout=config.dropout, 
                n_layers=config.n_layers,
                self_attention = config.self_attention,
                variation = config.variation
            )
        elif net_name == 'vae':
            self.model = VaeNet()

        self.y = []
        self.y_hat = []
        self.thresh = []
    
    def forward(self, examples, samples, gt_apps=None):
        return self.model(examples, samples, gt_apps)
    
    
    def training_step(self, batch, _):
        # examples | samples | gt_apps: (N, WINDOE_SIZE)
        _, examples, samples, gt_apps = batch
        loss = self(examples, samples, gt_apps)
        self.log('loss', loss, on_epoch=True, prog_bar=True, logger=True)
        return loss
    
    def validation_step(self, batch, _):
        # tags: (N, 3)
        # examples | samples | gt_apps: (N, WINDOE_SIZE)
        threshs, examples, samples, gt_apps = batch
        pred_apps = self(examples, samples)
        pred_apps[pred_apps < 15] = 0
        self.y.extend([tensor for tensor in pred_apps])
        self.y_hat.extend([tensor for tensor in gt_apps])
        self.thresh.extend([thresh for thresh in threshs])
    
    def on_validation_epoch_end(self):
        mae = torch.concat([y-y_hat for y, y_hat in zip(self.y, self.y_hat)]).abs().mean() 

        mae_on = torch.concat([y[y_hat>thresh] - y_hat[y_hat>thresh] for y, y_hat, thresh in zip(self.y, self.y_hat, self.thresh)]).abs().mean() 
        mre_on = torch.concat([(y[y_hat>thresh] - y_hat[y_hat>thresh]).abs() / y_hat[y_hat>thresh]
                                for y, y_hat, thresh in zip(self.y, self.y_hat, self.thresh)]).mean() 
        self.log('val_mae', mae, on_epoch=True, prog_bar=True, logger=True)
        self.log('val_mae_on', mae_on, on_epoch=True, prog_bar=True, logger=True)
        self.log('val_mre_on', mre_on, on_epoch=True, prog_bar=True, logger=True)
        self.y.clear()
        self.y_hat.clear()
        self.thresh.clear()
    
    def test_step(self, batch, _):
        threshs, examples, samples, gt_apps = batch
        pred_apps = self(examples, samples)
        pred_apps[pred_apps < 15] = 0
        self.y.extend([tensor for tensor in pred_apps])
        self.y_hat.extend([tensor for tensor in gt_apps])
        self.thresh.extend([thresh for thresh in threshs])

    def on_test_epoch_end(self):
        device = self.thresh[0].device
        y = reconstruct(self.y).to(device)
        y_hat = reconstruct(self.y_hat).to(device)
        mae = (y-y_hat).abs().mean()
        on_status = y_hat > self.thresh[0]
        mae_on = (y[on_status]-y_hat[on_status]).abs().mean() 
        mre_on = ((y[on_status]-y_hat[on_status]).abs() / y_hat[on_status]).mean() 
        self.log('test_mae', mae, on_epoch=True, prog_bar=True, logger=True)
        self.log('test_mae_on', mae_on, on_epoch=True, prog_bar=True, logger=True)
        self.log('test_mre_on', mre_on, on_epoch=True, prog_bar=True, logger=True)
        self.y.clear()
        self.y_hat.clear()
        self.thresh.clear()
    
    def configure_optimizers(self):
        optimizer = torch.optim.AdamW(self.parameters(), lr=1e-3)
        scheduler = {
            "scheduler": self.exponential_scheduler(
                optimizer,
                200,
                self.config.lr,
                self.config.min_lr
            ),
            "name": "learning_rate",
            "interval": "step",
            "frequency": 1
        }
        return [optimizer], [scheduler]
    
    @staticmethod
    def exponential_scheduler(optimizer, warmup_steps, lr, min_lr=1e-5, gamma=0.9999):
        def lr_lambda(x):
            if x > warmup_steps:
                if lr * gamma ** (x - warmup_steps) > min_lr:
                    return gamma ** (x - warmup_steps)
                else:
                    return min_lr / lr
            else:
                return x / warmup_steps

        return LambdaLR(optimizer, lr_lambda=lr_lambda)


def reconstruct(y):
    n = len(y)
    length = WINDOW_SIZE + (n - 1) * WINDOW_STRIDE 
    depth = WINDOW_SIZE // WINDOW_STRIDE
    out = torch.full([length, depth], float('nan'))
    for i, cur in enumerate(y):
        start = i * WINDOW_STRIDE
        d = i % depth
        out[start: start+WINDOW_SIZE, d] = cur
    out = torch.nanmedian(out, dim=-1).values
    return out