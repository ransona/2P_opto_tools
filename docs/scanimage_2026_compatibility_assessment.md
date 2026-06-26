# ScanImage 2026 Compatibility Assessment

This branch assesses `2P_OPTO_TOOLS` against the ScanImage tree copied to:

`/home/adamranson/code/SI_2026`

Previous ScanImage reference used by this repo:

`/home/adamranson/code/CUSTOM-SI-Premium_2023.1.1_-2025-06-27-_d280f351`

## Summary

Most control calls still map to properties and methods present in `SI_2026`. The main required code change is SLM point coordinate handling.

In the previous ScanImage reference, `StimulusField.slmPattern(:,1:2)` was stored as XY offsets relative to `StimulusField.centerXY`. Calling `recenterGalvoOntoSlmPattern()` converted those offsets to a weighted center and rewrote the offsets.

In `SI_2026`, `StimulusField.slmPattern(:,1:2)` is treated as absolute reference-space XY. `centerXY` is now used separately as the galvo/logspiral scanner offset. `SI_2026/+scanimage/+mroi/+scannerset/SLM.m` sets the scanner offset from `scanfield.centerXY`, then passes the absolute `slmPattern` coordinates to the SLM coordinate transform and phase-mask calculation.

This branch updates all 2P_OPTO_TOOLS SLM pattern builders to write absolute reference XY into `slmPattern` and to stop calling `recenterGalvoOntoSlmPattern()` after choosing `centerXY`.

## Source Checks

Checked in `SI_2026`:

- `+scanimage/+components/Photostim.m`
- `+scanimage/+mroi/+scanfield/+fields/StimulusField.m`
- `+scanimage/+mroi/+scannerset/SLM.m`
- `+scanimage/+mroi/RoiGroup.m`
- `+scanimage/+mroi/Roi.m`
- `+scanimage/+gui/SlmPatternGui.m`
- `+scanimage/+gui/+viewport/+display/+roi/SlmStimulusDisplay.m`
- `+scanimage/+components/IntegrationRoiManager.m`
- `+scanimage/+components/StackManager.m`
- `+scanimage/+components/MotionManager.m`
- `+scanimage/+components/Motors.m`

## Commands And Properties Used To Control ScanImage

### MATLAB Engine Launch And Attachment

Python starts MATLAB externally and connects via MATLAB Engine:

```matlab
addpath(genpath(<repo matlab path>))
cd(<path config directory>)
run('launch.m')
matlab.engine.shareEngine(<engine_name>)
```

Reconnect validation:

```matlab
global hSI hSICtl
assert(exist('hSI','var') == 1)
assert(exist('hSICtl','var') == 1)
assert(~isempty(hSI))
assert(isprop(hSI, 'hPhotostim'))
```

Status: still compatible. `hSI`, `hSICtl`, and `hPhotostim` exist in the same object model.

### Acquisition Start And Stop Scripts

Per-path scripts run by Python:

```matlab
cd(<path config directory>)
run('start_script.m')
run('stop_script.m')
```

The current scripts use:

```matlab
hSI.abort
hSI.startGrab
hSI.acqState
hSI.hScan2D.logFilePath
hSI.hScan2D.logFileStem
hSI.hScan2D.logFramesPerFile
hSI.hChannels.loggingEnable
```

Status: these properties and methods remain part of the SI component model. Live validation is still required because MDF/path-specific scanner names may differ.

### Schema-Driven Photostim Preparation

Top-level command emitted by Python:

```matlab
[importedPatternNames, importedPatternNumbers] = opto.scanimage.prepareSchemaPhotostim(hSI, schemaData, ...
    SequenceIndex=<seq_num>, ...
    TrialSequenceIndices=<trial_seq_nums>, ...
    PreStimPauseDuration=0.001, ...
    BlankDuration=0.001, ...
    ParkDuration=0.001, ...
    BlockDuration=<sequence_block_duration_s>, ...
    TriggerTerm=<photostim_trigger_term>, ...
    ConfigureSequence=true, ...
    StartPhotostim=true, ...
    PrefixBlankToSequence=false, ...
    EmbedBlankAndParkInStimGroup=false, ...
    NumSequences=1, ...
    MinCenterDistanceUm=15, ...
    Revolutions=5);
```

Main ScanImage API calls inside that MATLAB helper:

```matlab
hPs = hSI.hPhotostim
hPs.abort()
hPs.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1, 0)
hPs.sequenceSelectedStimuli = []
hPs.stimRoiGroups(end + 1) = scanimage.mroi.RoiGroup(...)
hPs.stimulusMode = 'sequence'
hPs.sequenceSelectedStimuli = [...]
hPs.numSequences = ...
hPs.autoTriggerPeriod = 0
hPs.stimImmediately = false
hPs.monitoring = true
hPs.logging = true
hPs.stimTriggerTerm = ...
hPs.start()
```

Stimulus group creation:

```matlab
hGroup = scanimage.mroi.RoiGroup(<name>)
roi = scanimage.mroi.Roi()
sf = scanimage.mroi.scanfield.fields.StimulusField()
sf.centerXY = <galvo center in reference coordinates>
sf.sizeXY = <spiral size in reference coordinates>
sf.duration = <seconds>
sf.repetitions = 1
sf.stimfcnhdl = @scanimage.mroi.stimulusfunctions.logspiral
sf.stimparams = {'revolutions', 5, 'direction', 'outward'}
sf.slmPattern = [absolute_ref_x absolute_ref_y z weight]
sf.powers = [beam powers in percent]
roi.add(0, sf)
hGroup.add(roi)
```

Status: compatible after this branch's coordinate update. `Photostim`, `RoiGroup`, `Roi`, and `StimulusField` APIs still exist. The required semantic change is absolute `slmPattern` XY.

### Trial Photostim Trigger Preparation

Python emits a command that checks the prepared ScanImage sequence state:

```matlab
hPs = hSI.hPhotostim
currentSequence = hPs.sequenceSelectedStimuli
currentPosition = hPs.sequencePosition
hPs.numSequences = 1
hPs.stimImmediately = false
if ~hPs.active
    hPs.start()
end
```

Status: compatible. `sequenceSelectedStimuli`, `sequencePosition`, `numSequences`, `stimImmediately`, `active`, and `start()` are present in `SI_2026/+scanimage/+components/Photostim.m`.

### Trial Waveform For Stimulus-Group Advance

Python emits:

```matlab
do_task = opto.scanimage.testVdaqDoTriggeredByDi( ...
    'outputLine', <trial_waveform_output_port>, ...
    'startTrigger', <trial_waveform_start_trigger_port or ''>, ...
    'sampleRate_Hz', <sample_rate>, ...
    'pulseTimes_s', trialTriggerTimesSec, ...
    'pulseWidth_s', trialPulseWidthSec, ...
    'taskName', 'Opto Photostim Trial DO', ...
    'taskVarName', 'optoPhotostimTrialDoTask', ...
    'startTriggerEdge', <edge>, ...
    'autoStart', false);
do_task.sampleCallbackAutoRead = false;
do_task.sampleCallbackN = 1;
do_task.sampleCallback = @(varargin) evalin('base', <callback>);
do_task.start();
```

`testVdaqDoTriggeredByDi` uses:

```matlab
resourceStore = dabs.resources.ResourceStore()
vDAQ = resourceStore.filterByName('vDAQ0')
do_task = dabs.vidrio.ddi.DoTask(vDAQ.hDevice, <taskName>)
do_task.addChannel(<line>, 'Photostim Trigger Out')
do_task.writeOutputBuffer(outputWaveform)
do_task.sampleRate = <Hz>
do_task.sampleMode = 'finite'
do_task.samplesPerTrigger = numel(outputWaveform)
do_task.allowRetrigger = false
do_task.startTrigger = <line or ''>
do_task.startTriggerEdge = <edge>
do_task.start()
do_task.abort()
delete(do_task)
```

Status: likely compatible, but live validation is required. The vDAQ `DoTask` class remains in the copied ScanImage tree and no source-level mismatch was found in this audit.

### Trial Time-Zero Snapshot Callback

The waveform task callback records integration state at the moment the DO task starts:

```matlab
hSI_cb = hSI
irm_cb = hSI_cb.hIntegrationRoiManager
roiNames_cb = {irm_cb.intParams.intRois.name}
cursor_cb = double(irm_cb.integrationValueCursor)
frameHist_cb = double(irm_cb.integrationFrameNumberHistory)
timeHist_cb = double(irm_cb.integrationTimestampHistory)
optoPhotostimTrialIntegrationSnapshot = struct(...)
optoPhotostimTrialDoTaskStarted = true
```

Status: likely compatible. `IntegrationRoiManager` still exposes integration ROI history/cursor state, but live validation should confirm scalar-vs-vector shape behavior on this version.

### Photostim Status And Abort

Status command:

```matlab
hPs = hSI.hPhotostim
hPs.active
hPs.sequencePosition
hPs.completedSequences
hPs.sequenceSelectedStimuli
hPs.status
```

Abort command:

```matlab
if hPs.active
    hPs.abort()
end
```

Status: compatible.

### Online Analysis ROI Creation And Polling

Configuration command uses:

```matlab
hInt = hSI.hIntegrationRoiManager
imagingRoiGroup = hSI.hRoiManager.currentRoiGroup
sf = scanimage.mroi.scanfield.fields.IntegrationField()
sf.centerXY = <target reference XY>
sf.sizeXY = <diameter in reference coordinates>
sf.channel = <channel>
sf.processor = 'cpu'
sf.mask = <binary mask>
sf.owningImagingScanField(imagingRoiGroup, zValue, 'centeronly')
roi = scanimage.mroi.Roi()
roi.name = <name>
roi.add(zValue, sf)
rg = scanimage.mroi.RoiGroup('OptoSchema Online Activity')
rg.add(roi)
hInt.enable = false
hInt.enableDisplay = false
hInt.integrationHistoryLength = <N>
hInt.roiGroup = rg
hInt.enable = numel(rg.rois) > 0
```

Polling command reads:

```matlab
hInt.enable
hInt.integrationValueHistoryPostProcessed
hInt.integrationTimestampHistory
hInt.integrationFrameNumberHistory
hInt.integrationValueCursor
hInt.intParams.intRois
```

Status: likely compatible. No class/property removal was found in source inspection. Runtime testing should verify history matrix dimensions for one ROI and many ROIs.

### SLM PSF Volume Diagnostic

Setup uses:

```matlab
hSI.active
hSI.hMotors.samplePosition
hSI.hMotionManager.enable = false
hSI.hRoiManager.roiGroupMroi
hSI.hRoiManager.mroiEnable = true
hSI.hStackManager.enable = true
hSI.hStackManager.stackMode = 'slow'
hSI.hStackManager.stackActuator = 'motor'
hSI.hStackManager.stackDefinition = 'uniform'
hSI.hStackManager.centeredStack = true
hSI.hStackManager.stackZStepSize = <um>
hSI.hStackManager.numSlices = <N>
hSI.hStackManager.framesPerSlice = <N>
hSI.hScan2D.logAverageFactor = <frames_per_slice>
hSI.hDisplay.displayRollingAverageFactor = <N>
hSI.hDisplay.displayRollingAverageFactorLock = true
hSI.hChannels.loggingEnable = true
```

Per-volume command uses:

```matlab
hSI.hMotors.moveSample([NaN NaN <z_um>])
hPs.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1, 0)
hPs.stimulusMode = 'sequence'
hPs.sequenceSelectedStimuli = 1
hPs.numSequences = inf
hPs.stimImmediately = false
hPs.monitoring = false
hPs.logging = false
hPs.stimTriggerTerm = 'frame'
hPs.start()
hSI.hScan2D.logFilePath = <volume_dir>
hSI.hScan2D.logFileStem = 'volume'
hSI.startGrab()
```

Status: partly source-compatible, but live validation is required. The stack properties still exist in `SI_2026`, but previous testing showed stack configuration is sensitive to read-only properties and must use `uniform` stack mode with motor actuation.

### Diagnostic Photostim Grid And Test Pattern

These commands create a single `RoiGroup` with pause/stim/park fields, configure:

```matlab
hPs.stimulusMode = 'sequence'
hPs.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1, 0)
hPs.sequenceSelectedStimuli = 1
hPs.numSequences = Inf
hPs.stimImmediately = true
hPs.start()
```

Status: compatible after this branch's absolute `slmPattern` update.

## Required Code Changes Made On This Branch

Changed:

- `matlab/+opto/+scanimage/prepareSchemaPhotostim.m`
- `src/opto_schema_gui/matlab_bridge.py`

Specific change:

- `sf.slmPattern(:,1:2)` now receives absolute reference XY.
- Calls to `recenterGalvoOntoSlmPattern()` were removed from programmatic SLM builders after explicit `centerXY` selection.
- PSF volume diagnostic now uses `sf.slmPattern = [centerRef z 1]`, not `[0 0 z 1]`.

## Remaining Risks

- vDAQ `DoTask` behavior should be tested on the rig because the class is external hardware-facing.
- Online analysis should be tested for one ROI and multiple ROIs to confirm `integration*History` array shapes.
- Stack diagnostic should be tested live because stack configuration depends on the active MDF/scanner configuration.
- Legacy `importSchemaPatterns` still builds non-SLM point fields by default (`stimulus_function = point`). The normal experiment path uses `prepareSchemaPhotostim`, not this legacy importer. If the old importer will be used for SLM stimulation, it should be upgraded separately rather than assumed equivalent.
