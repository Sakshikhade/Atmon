"""EdgeFace-XS-GAMMA via timm EdgeNeXt + low-rank Linear (Idiap).

Copyright (C) 2023 Anjith George, Idiap Research Institute.
Distributed under CC BY-NC-SA 4.0 — see the EdgeFace LICENSE.
Minimal vendored copy of backbones/timmfr.py + get_model for edgeface_xs_gamma_06.
"""

import torch
import torch.nn as nn


class LoRaLin(nn.Module):
    def __init__(self, in_features, out_features, rank, bias=True):
        super().__init__()
        self.linear1 = nn.Linear(in_features, rank, bias=False)
        self.linear2 = nn.Linear(rank, out_features, bias=bias)

    def forward(self, x):
        return self.linear2(self.linear1(x))


def _replace_linear_with_lowrank(model, rank_ratio=0.6):
    for name, module in model.named_children():
        if isinstance(module, nn.Linear) and "head" not in name:
            in_features = module.in_features
            out_features = module.out_features
            rank = max(2, int(min(in_features, out_features) * rank_ratio))
            bias = module.bias is not None
            setattr(model, name, LoRaLin(in_features, out_features, rank, bias))
        else:
            _replace_linear_with_lowrank(module, rank_ratio)
    return model


class TimmFRWrapper(nn.Module):
    def __init__(self, model_name="edgenext_x_small", featdim=512):
        super().__init__()
        import timm

        self.model = timm.create_model(model_name)
        self.model.reset_classifier(featdim)

    def forward(self, x):
        return self.model(x)


def get_edgeface_xs_gamma_06():
    """Uninitialized EdgeFace-XS (γ=0.6) matching the HF checkpoint layout."""
    return _replace_linear_with_lowrank(
        TimmFRWrapper("edgenext_x_small", featdim=512), rank_ratio=0.6
    )


def load_edgeface_xs_gamma_06(checkpoint_path, map_location="cpu"):
    model = get_edgeface_xs_gamma_06()
    state = torch.load(checkpoint_path, map_location=map_location, weights_only=True)
    model.load_state_dict(state, strict=True)
    model.eval()
    return model
