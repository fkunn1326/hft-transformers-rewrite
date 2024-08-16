import json
import os
from typing import List

import torch
import torch.utils.data as data
from pydantic import BaseModel, TypeAdapter

from preprocess.midi import LABELS
from training.config import DatasetConfig


class Metadata(BaseModel):
    canonical_composer: str
    canonical_title: str
    split: str
    year: int
    midi_filename: str
    audio_filename: str
    duration: float


class FrameInfomation(BaseModel):
    onset_frame: int
    offset_frame: int


class DatasetItem(BaseModel):
    basename: str
    feature: FrameInfomation
    label: FrameInfomation


class Dataset(data.Dataset):
    def __init__(
        self,
        dir: str,
        num_frames: int = 128,
    ):
        datamapping_path = os.path.join(dir, "mapping.json")
        with open(datamapping_path, "r") as f:
            self.datamapping = TypeAdapter(List[DatasetItem]).validate_json(f.read())
        config_path = os.path.join(dir, "config.json")
        with open(config_path, "r") as f:
            self.config = DatasetConfig.model_validate_json(f.read())
        self.features_dir = os.path.join(dir, "features")
        self.labels_dir = os.path.join(dir, "labels")
        self.num_frames = num_frames

        self.features = {}
        self.labels = {}

        for mapping in self.datamapping:
            basename = mapping.basename
            if basename in self.features:
                continue
            feature_path = os.path.join(self.features_dir, basename + ".pt")
            self.features[basename] = torch.load(
                feature_path, map_location="cpu", weights_only=True
            )
            data = {}
            for label in LABELS:
                label_path = os.path.join(self.labels_dir, basename + f".{label}.json")
                with open(label_path, "r") as f:
                    arr = json.load(f)
                    data[label] = torch.tensor(arr)
            self.labels[basename] = data

    def __getitem__(self, idx: int):
        mapping = self.datamapping[idx]
        feature_path = os.path.join(self.features_dir, mapping.basename + ".pt")
        labels = {}
        for label in LABELS:
            label_path = os.path.join(
                self.labels_dir, mapping.basename + f".{label}.json"
            )
            with open(label_path, "r") as f:
                arr = json.load(f)
                labels[label] = torch.tensor(arr)

        feature: torch.Tensor = torch.load(
            feature_path, map_location="cpu", weights_only=True
        )

        zero_value = torch.log(torch.tensor(self.config.feature.log_offset))
        if mapping.feature.onset_frame < 0:
            pad = torch.zeros(
                -mapping.feature.onset_frame, feature.shape[1], dtype=feature.dtype
            )
            feature = torch.cat([pad.fill_(zero_value), feature], dim=0)
            mapping.feature.onset_frame = 0
            mapping.feature.offset_frame = (
                mapping.feature.offset_frame - mapping.feature.onset_frame
            )

        feature = feature[mapping.feature.onset_frame : mapping.feature.offset_frame]
        if feature.shape[0] < self.num_frames:
            pad = torch.zeros(
                self.num_frames - feature.shape[0],
                feature.shape[1],
                dtype=feature.dtype,
            )
            feature = torch.cat([feature, pad.fill_(zero_value)], dim=0)

        spec = feature.T
        for label in labels:
            tensor = labels[label]
            if tensor.shape[0] < self.num_frames:
                pad = torch.zeros(
                    self.num_frames - tensor.shape[0],
                    tensor.shape[1],
                    dtype=tensor.dtype,
                )
                tensor = torch.cat([tensor, pad], dim=0)
            labels[label] = tensor

        onset = labels["onset"]
        offset = labels["offset"]
        mpe = labels["mpe"].float()
        velocity = labels["velocity"].long()

        return spec, onset, offset, mpe, velocity

    def __len__(self):
        return len(self.datamapping)

    def collate_fn(self, batch):
        specs, onsets, offsets, mpes, velocities = zip(*batch)
        specs = torch.stack(specs)
        onsets = torch.stack(onsets)
        offsets = torch.stack(offsets)
        mpes = torch.stack(mpes)
        velocities = torch.stack(velocities)
        return specs, onsets, offsets, mpes, velocities
