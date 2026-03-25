# ScanImage pattern import

This repo now contains a first-pass ScanImage bridge for importing schema `patterns` as ScanImage photostimulation stimulus groups.

Files:

- `scripts/schema_to_json.py`
- `matlab/+opto/+scanimage/loadSchemaYaml.m`
- `matlab/+opto/+scanimage/buildStimRoiGroupFromPattern.m`
- `matlab/+opto/+scanimage/importSchemaPatterns.m`

## What it does

- Reads the YAML schema from this repo.
- Creates one `scanimage.mroi.RoiGroup` per schema pattern.
- Creates one `scanimage.mroi.scanfield.fields.StimulusField` ROI per cell in the pattern.
- Appends each imported group to `hSI.hPhotostim.stimRoiGroups`.

The implementation follows the public ScanImage API examples for `StimulusField`, `Roi`, and `RoiGroup`.

## Important limitations

This is intentionally only the first bridge. Two parts are microscope-specific and are not solved generically:

1. Coordinate transform

Schema `x,y,z` values are experimental/sample coordinates. ScanImage stimulus ROIs need ScanImage stimulus coordinates. The importer therefore requires transform callbacks:

- `XYTransform`
- `ZTransform`

You need to provide those using your scanner/SLM alignment.

2. Frequency mapping

The schema stores `frequency_hz`, but the public `StimulusField` API shown in ScanImage docs exposes:

- `duration`
- `repetitions`
- `powers`
- `stimfcnhdl`

There is no one-line public API mapping from schema frequency to pulsed laser output without either:

- a custom beam power function, or
- expanding each pattern into an explicit pulse train policy

For that reason the first importer defaults to `IgnoreFrequency=true` and imports each cell as a continuous dwell stimulus with the schema duration and power.

That is enough to establish the stimulus group import path cleanly, but it is not yet the final temporal behavior you want.

## Example MATLAB usage

```matlab
schemaPath = "/home/adamranson/code/2P_opto_tools/data/TEST/DEFAULT/schema.yaml";

xyTransform = @(xyz)[xyz(1) xyz(2)];
zTransform = @(xyz)xyz(3);

imported = opto.scanimage.importSchemaPatterns(hSI, schemaPath, ...
    ClearExisting=true, ...
    XYTransform=xyTransform, ...
    ZTransform=zTransform, ...
    PauseDuration=0.010, ...
    ParkDuration=0.010, ...
    PointSizeXY=[0 0], ...
    StimulusFunction="point", ...
    IgnoreFrequency=true);
```

## Why this is the right first step

The public ScanImage documentation states that photostimulation stimulus sequences are built from `RoiGroup` objects containing `StimulusField` ROIs, and that these are registered in `hSI.hPhotostim.stimRoiGroups`.

Sources:

- https://docs.scanimage.org/Advanced%2BFeatures/ScanImage%2BAPI/Stimulus%2BField%2BAPI.html
- https://docs.scanimage.org/Premium%2BFeatures/Photostimulation.html
- https://docs.scanimage.org/Tab%2BReference%2BGuide/Photostim.html

## Current local blocker

The ScanImage checkout at:

- `/home/adamranson/code/CUSTOM-SI-Premium_2023.1.1_-2025-06-27-_d280f351`

currently only contains a `.git` directory and no checked-out MATLAB source tree, so I could not wire this directly into that checkout yet.
