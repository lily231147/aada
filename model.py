"""
MIT License
Copyright (c) 2024-present lily.

written by lily
email: lily231147@gmail.com
"""

import sys
sys.path.append('/home/aistudio/external-libraries')

import lightning as L
import torch
from torch import nn, optim
from torch.nn import functional as F
from torchmetrics.regression import MeanAbsoluteError

WINDOW_SIZE = 1024
WINDOW_STRIDE = 256



class DownSampleNetwork(nn.Module):
    def __init__(self, inplates, midplates):
        super().__init__()
        self.identity = nn.Sequential(
            nn.Conv1d(in_channels=inplates, out_channels=inplates, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm1d(inplates),
        )
        self.stream = nn.Sequential(
            nn.Conv1d(in_channels=inplates, out_channels=midplates, kernel_size=3, stride=2, padding=1),
            nn.BatchNorm1d(midplates),
            nn.ReLU(),
            nn.Conv1d(in_channels=midplates, out_channels=midplates,kernel_size=3, stride=1, padding=1),
            nn.BatchNorm1d(midplates),
            nn.ReLU(),
            nn.Conv1d(in_channels=midplates, out_channels=inplates, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm1d(inplates)

        )
        self.relu = nn.ReLU()

    def forward(self, x):
        x = self.stream(x) + self.identity(x)
        return self.relu(x)


class UpSampleNetwork(nn.Module):
    def __init__(self, inplates, midplates):
        super().__init__()
        self.identity = nn.Sequential(
            nn.ConvTranspose1d(in_channels=inplates, out_channels=inplates, kernel_size=3, stride=2, padding=1, output_padding=1),
            nn.BatchNorm1d(inplates),
        )
        self.stream = nn.Sequential(
            nn.ConvTranspose1d(in_channels=inplates, out_channels=midplates, kernel_size=3, stride=2, padding=1, output_padding=1),
            nn.BatchNorm1d(midplates),
            nn.ReLU(),
            nn.Conv1d(in_channels=midplates, out_channels=midplates,kernel_size=3, stride=1, padding=1),
            nn.BatchNorm1d(midplates),
            nn.ReLU(),
            nn.Conv1d(in_channels=midplates, out_channels=inplates, kernel_size=3, stride=1, padding=1),
            nn.BatchNorm1d(inplates)
        )
        self.relu = nn.ReLU()

    def forward(self, x):
        x = self.stream(x) + self.identity(x)
        return self.relu(x)
    
class PositionEmbeddingSine(nn.Module):
    def __init__(self, dropout=0.1):
        super().__init__()
        self.dropout = nn.Dropout(p=dropout)

    def forward(self, x):
        inplates, length = x.shape[1], x.shape[2]
        pe = torch.zeros(length, inplates) 
        position = torch.arange(0, length)
        div_term = torch.full([1, inplates // 2], 10000).pow((torch.arange(0, inplates, 2) / inplates))
        pe[:, 0::2] = torch.sin(position[:, None] / div_term)
        pe[:, 1::2] = torch.cos(position[:, None] / div_term)
        return self.dropout(pe.permute(1, 0).to(x.device))
    

class Attention(nn.Module):
    def __init__(self, d_model, n_heads) -> None:
        super().__init__()
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_head = d_model // n_heads

        self.wq = nn.Linear(d_model, d_model, bias=False)
        self.wk = nn.Linear(d_model, d_model, bias=False)
        self.wv = nn.Linear(d_model, d_model, bias=False)
        self.out = nn.Linear(d_model, d_model, bias=False)

    def forward(self, q,k,v):
        N = q.shape[0]
        q = self.wq(q).reshape([N, -1, self.n_heads, self.d_head])
        k = self.wk(k).reshape([N, -1, self.n_heads, self.d_head])
        v = self.wv(v).reshape([N, -1, self.n_heads, self.d_head])

        atten = torch.einsum('nqhd,nkhd->nhqk', q, k)
        atten = atten / (self.d_model ** 0.5)
        atten = torch.softmax(atten, dim=-1)

        v = torch.einsum('nhqk,nkhd->nqhd', atten, v).reshape([N, -1, self.d_model])
        return self.out(v)
    
class ExampleEncoderLayer(nn.Module):
    def __init__(self, inplates, midplates, n_heads, dropout) -> None:
        super().__init__()
        self.down_sampler = DownSampleNetwork(inplates, midplates)
        self.attention = Attention(inplates, n_heads)
        self.norm = nn.BatchNorm1d(inplates)
        self.dropout = nn.Dropout(dropout)
    
    def forward(self, x):
        """
        Args:
            x (N, D, L): the features of examples to each encoder layer
        """
        x = self.down_sampler(x)
        x = self.norm(x).permute(0, 2, 1)
        x = x + self.dropout(self.attention(x, x, x))
        return x.permute(0, 2, 1)
    

class SampleEncoderLayer(nn.Module):
    def __init__(self, inplates, midplates, n_heads, dropout) -> None:
        super().__init__()
        self.down_sampler = DownSampleNetwork(inplates, midplates)
        self.attention1 = Attention(inplates, n_heads)
        self.attention2 = Attention(inplates, n_heads)
        self.norm1 = nn.BatchNorm1d(inplates)
        self.norm2 = nn.BatchNorm1d(inplates)
        self.dropout1 = nn.Dropout(dropout)
        self.dropout2 = nn.Dropout(dropout)
    
    def forward(self, x, y):
        """
        Args:
            x (N, D, L): the features of samples
            y (N, D, L): the features of examples
        """
        y = y.permute(0, 2, 1)
        x = self.down_sampler(x)
        x = self.norm1(x).permute(0, 2, 1)
        x = x + self.dropout1(self.attention1(x, x, x))
        x = self.norm2(x.permute(0, 2, 1)).permute(0, 2, 1)
        x = x + self.dropout2(self.attention2(x, y, y))
        return x.permute(0, 2, 1)
    
class DecoderLayer(nn.Module):
    def __init__(self, inplates, midplates, n_heads, dropout) -> None:
        super().__init__()
        self.up_sampler = UpSampleNetwork(inplates, midplates)
        self.attention1 = Attention(inplates, n_heads)
        self.attention2 = Attention(inplates, n_heads)
        self.norm1 = nn.BatchNorm1d(inplates)
        self.norm2 = nn.BatchNorm1d(inplates)
        self.dropout1 = nn.Dropout(dropout)
        self.dropout2 = nn.Dropout(dropout)
    
    def forward(self, x, y):
        """
        Args:
            x (N, D, L): the features of decoder
            y (N, D, L): the features of samples
        """
        y = y.permute(0, 2, 1)
        x = self.up_sampler(x)
        x = self.norm1(x).permute(0, 2, 1)
        x = x + self.dropout1(self.attention1(x, x, x))
        x = self.norm2(x.permute(0, 2, 1)).permute(0, 2, 1)
        x = x + self.dropout2(self.attention2(x, y, y))
        return x.permute(0, 2, 1)
    

class ExampleEncoder(nn.Module):
    def __init__(self, inplates, midplates, n_heads, dropout, n_layers) -> None:
        super().__init__()
        self.layers = nn.ModuleList([ExampleEncoderLayer(inplates, midplates, n_heads, dropout) for _ in range(n_layers)])
    
    def forward(self, x):
        examples = []
        for layer in self.layers:
            x = layer(x)
            examples.append(x)
        return examples

class SampleEncoder(nn.Module):
    def __init__(self, inplates, midplates, n_heads, dropout, n_layers) -> None:
        super().__init__()
        self.layers = nn.ModuleList([SampleEncoderLayer(inplates, midplates, n_heads, dropout) for _ in range(n_layers)])
    
    def forward(self, x, examples):
        samples = []
        for example, layer in zip(examples, self.layers):
            x = layer(x, example)
            samples.append(x)
        return examples
    
class Decoder(nn.Module):
    def __init__(self, inplates, midplates, n_heads, dropout, n_layers) -> None:
        super().__init__()
        self.layers = nn.ModuleList([DecoderLayer(inplates, midplates, n_heads, dropout) for _ in range(n_layers)])
    
    def forward(self, x, samples):
        for sample, layer in zip(samples, self.layers):
            x = layer(x, sample)
        return x
    
class AadaNet(L.LightningModule):
    def __init__(self, inplates, midplates, n_heads, dropout, n_layers) -> None:
        super().__init__()
        self.up_dim1 = nn.Conv1d(in_channels=1, out_channels=inplates, kernel_size=3, stride=1, padding=1)
        self.up_dim2 = nn.Conv1d(in_channels=1, out_channels=inplates, kernel_size=3, stride=1, padding=1)
        self.down_dim = nn.Conv1d(in_channels=inplates, out_channels=1, kernel_size=3, stride=1, padding=1)

        self.pe1 = PositionEmbeddingSine()
        self.pe2 = PositionEmbeddingSine()

        self.example_encoder = ExampleEncoder(inplates, midplates, n_heads, dropout, n_layers)
        self.sample_encoder = SampleEncoder(inplates, midplates, n_heads, dropout, n_layers)
        self.decoder = Decoder(inplates, midplates, n_heads, dropout, n_layers)

        self.y = []
        self.y_hat = []
        self.thresh = []

    def forward(self, examples, samples):
        """
        Args:
            examples (N, L): input examples
            samples (N, L): input samples
        """
        # examples | samples: (N, D, L)
        examples = self.up_dim1(examples[:, None, :])
        samples = self.up_dim2(samples[:, None, :])
        examples = examples + self.pe1(examples)
        samples = samples + self.pe2(samples)

        examples = self.example_encoder(examples)
        samples = self.sample_encoder(samples, examples)
        appliance = self.decoder(samples[-1], samples)
        return self.down_dim(appliance).squeeze(1)
    
    def configure_optimizers(self):
        optimizer = optim.AdamW(self.parameters(), lr=1e-4)
        return optimizer
    
    def training_step(self, batch, _):
        # examples | samples | gt_apps: (N, WINDOE_SIZE)
        _, examples, samples, gt_apps = batch
        pred_apps = self(examples, samples)
        mse = F.mse_loss(pred_apps, gt_apps)
        self.log('train_loss', mse, on_step=True, on_epoch=True, prog_bar=True, logger=True)
        return mse
    
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
        self.log('val_mae', mae, on_epoch=True, prog_bar=True, logger=True)
        self.log('val_mae_on', mae_on, on_epoch=True, prog_bar=True, logger=True)
        self.y.clear()
        self.y_hat.clear()
        self.thresh.clear()
    
    def test_step(self, batch, _):
        threshs, examples, samples, gt_apps = batch
        pred_apps = self(examples, samples)
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
        self.log('test_mae', mae, on_epoch=True, prog_bar=True, logger=True)
        self.log('test_mae_on', mae_on, on_epoch=True, prog_bar=True, logger=True)
        self.y.clear()
        self.y_hat.clear()
        self.thresh.clear()

    

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

    
    
