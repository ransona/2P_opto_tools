function hRoiGroup = buildStimRoiGroupFromPattern(pattern, opts)
arguments
    pattern (1,1) struct
    opts.StimulusFunction (1,1) string = "point"
    opts.PointSizeXY (1,2) double = [0 0]
    opts.RotationDegrees (1,1) double = 0
    opts.PauseDuration (1,1) double = 0.010
    opts.ParkDuration (1,1) double = 0.010
    opts.XYTransform function_handle = @(xyz)[xyz(1) xyz(2)]
    opts.ZTransform function_handle = @(xyz)xyz(3)
    opts.PowerScaleMode (1,1) string = "multiply"
    opts.IgnoreFrequency (1,1) logical = true
end

hRoiGroup = scanimage.mroi.RoiGroup();
if isprop(hRoiGroup, 'name') && isfield(pattern, 'name')
    hRoiGroup.name = pattern.name;
end

if opts.IgnoreFrequency && isfield(pattern, 'frequency_hz')
    warning( ...
        'Schema frequency_hz for pattern "%s" is not yet mapped into ScanImage timing. Group imported as continuous dwell stimuli only.', ...
        pattern.name);
end

if opts.PauseDuration > 0
    hRoiPause = makeZeroPowerPointRoi(opts.PauseDuration);
end

if opts.ParkDuration > 0
    hRoiPark = makeZeroPowerPointRoi(opts.ParkDuration);
end

for idx = 1:numel(pattern.cells)
    cellSpec = pattern.cells(idx);
    xyz = [cellSpec.x cellSpec.y cellSpec.z];
    centerXY = opts.XYTransform(xyz);
    zValue = opts.ZTransform(xyz);

    if numel(centerXY) ~= 2
        error('XYTransform must return a 1x2 vector for pattern "%s", cell %d.', pattern.name, idx);
    end

    if opts.PauseDuration > 0
        hRoiGroup.add(hRoiPause.copy());
    end

    hStimField = scanimage.mroi.scanfield.fields.StimulusField();
    hStimField.centerXY = centerXY;
    hStimField.sizeXY = opts.PointSizeXY;
    hStimField.rotationDegrees = opts.RotationDegrees;
    hStimField.stimfcnhdl = str2func(char("scanimage.mroi.stimulusfunctions." + opts.StimulusFunction));
    hStimField.duration = pattern.duration_s;
    hStimField.repetitions = 1;
    hStimField.powers = resolvePowers(pattern, cellSpec, opts.PowerScaleMode);

    hRoi = scanimage.mroi.Roi();
    hRoi.add(zValue, hStimField);
    hRoiGroup.add(hRoi);
end

if opts.ParkDuration > 0
    hRoiGroup.add(hRoiPark);
end


function powers = resolvePowers(pattern, cellSpec, powerScaleMode)
basePower = pattern.power_percent;

switch lower(powerScaleMode)
    case "multiply"
        powers = basePower .* cellSpec.power_scale;
    case "absolute"
        powers = cellSpec.power_scale;
    otherwise
        error('Unknown PowerScaleMode "%s".', powerScaleMode);
end
end


function hRoi = makeZeroPowerPointRoi(durationSeconds)
hSfPoint = scanimage.mroi.scanfield.fields.StimulusField();
hSfPoint.centerXY = [0 0];
hSfPoint.sizeXY = [0 0];
hSfPoint.stimfcnhdl = @scanimage.mroi.stimulusfunctions.point;
hSfPoint.duration = durationSeconds;
hSfPoint.repetitions = 1;
hSfPoint.powers = [0 0 0];

hRoi = scanimage.mroi.Roi();
hRoi.add(0, hSfPoint);
end
end
