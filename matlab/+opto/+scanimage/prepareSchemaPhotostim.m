function [importedPatternNames, patternNumbers] = prepareSchemaPhotostim(hSI, schemaSource, opts)
arguments
    hSI
    schemaSource
    opts.PythonExecutable (1,1) string = "python"
    opts.PreStimPauseDuration (1,1) double = 0.001
    opts.BlankDuration (1,1) double = 0.001
    opts.ParkDuration (1,1) double = 0.001
    opts.TriggerTerm string = ""
    opts.MinCenterDistanceUm (1,1) double = 15
    opts.Revolutions (1,1) double = 5
end

if ~isprop(hSI, 'hPhotostim') || isempty(hSI.hPhotostim)
    error('The provided hSI handle does not expose hPhotostim.');
end

disp('prepareSchemaPhotostim: resolving schema');
if isstruct(schemaSource)
    schema = schemaSource;
elseif ischar(schemaSource) || isstring(schemaSource)
    schemaPath = string(schemaSource);
    schema = opto.scanimage.loadSchemaYaml(schemaPath, opts.PythonExecutable);
else
    error('prepareSchemaPhotostim requires schemaSource to be a struct or path string.');
end
disp('prepareSchemaPhotostim: schema ready');
if ~isfield(schema, 'patterns') || ~isfield(schema, 'sequences')
    error('Schema data must contain patterns and sequences blocks.');
end

patternNames = string(fieldnames(schema.patterns));
sequenceNames = string(fieldnames(schema.sequences));

hPs = hSI.hPhotostim;
disp('prepareSchemaPhotostim: photostim handle ready');
if hPs.active
    disp('prepareSchemaPhotostim: aborting active photostim');
    hPs.abort();
    disp('prepareSchemaPhotostim: active photostim aborted');
end
disp('prepareSchemaPhotostim: clearing existing stimulus groups');
hPs.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1, 0);
hPs.sequenceSelectedStimuli = [];
disp('prepareSchemaPhotostim: existing stimulus groups cleared');

nBeams = getPhotostimBeamCount(hSI);
disp('prepareSchemaPhotostim: beam count resolved');
disp(nBeams);
disp('Preparing reserved stimulus group: BLANK');
hPs.stimRoiGroups(end + 1) = makeBlankOnlyGroup("BLANK", opts.BlankDuration, nBeams);
disp('Preparing reserved stimulus group: PARK');
hPs.stimRoiGroups(end + 1) = makeParkOnlyGroup("PARK", opts.ParkDuration, nBeams);

importedPatternNames = strings(0, 1);
patternNumbers = zeros(0, 1);

schemaPatternNames = string(fieldnames(schema.patterns));
usedPatternNames = strings(0, 1);
usedPatternNumbers = zeros(0, 1);

for idx = 1:numel(sequenceNames)
    sequenceName = sequenceNames(idx);
    sequenceField = char(sequenceName);
    sequence = schema.sequences.(sequenceField);
    disp("Preparing schema sequence:");
    disp(sequenceName);
    disp(idx);
    hGroup = buildSequenceStimGroup(sequenceName, sequence, schema, hSI, opts, schemaPatternNames);
    hPs.stimRoiGroups(end + 1) = hGroup;

    for stepIdx = 1:numel(sequence.steps)
        stepPatternName = string(sequence.steps(stepIdx).pattern);
        if any(usedPatternNames == stepPatternName)
            continue;
        end
        patternNumber = find(schemaPatternNames == stepPatternName, 1, 'first');
        if isempty(patternNumber)
            error('Could not resolve schema pattern number for pattern "%s".', stepPatternName);
        end
        usedPatternNames(end + 1, 1) = stepPatternName; %#ok<AGROW>
        usedPatternNumbers(end + 1, 1) = patternNumber; %#ok<AGROW>
    end
end

importedPatternNames = usedPatternNames;
patternNumbers = usedPatternNumbers;

hPs.stimulusMode = 'onDemand';
hPs.sequenceSelectedStimuli = [];
hPs.numSequences = 1;
if strlength(opts.TriggerTerm) > 0
    hPs.stimTriggerTerm = normalizePhotostimTriggerTerm(char(opts.TriggerTerm));
end

disp('Starting photostim mask generation');
hPs.start();
disp('Photostim mask generation ready');
end


function hGroup = buildSequenceStimGroup(sequenceName, sequence, schema, hSI, opts, schemaPatternNames)
nBeams = getPhotostimBeamCount(hSI);
hGroup = scanimage.mroi.RoiGroup(char(sequenceName));
hGroup.add(makePauseRoi([0 0], [0 0], opts.PreStimPauseDuration, nBeams));

orderedSteps = sequence.steps;
if isempty(orderedSteps)
    error('Sequence "%s" contains no steps.', string(sequenceName));
end

cursorTime = 0.0;
for stepIdx = 1:numel(orderedSteps)
    step = orderedSteps(stepIdx);
    patternName = string(step.pattern);
    if ~isfield(schema.patterns, char(patternName))
        error('Sequence "%s" references unknown pattern "%s".', string(sequence.name), patternName);
    end
    pattern = schema.patterns.(char(patternName));
    patternNumber = find(schemaPatternNames == patternName, 1, 'first');
    if isempty(patternNumber)
        error('Could not resolve schema pattern number for pattern "%s".', patternName);
    end
    if step.start_s < cursorTime - 1e-9
        error( ...
            'Sequence "%s" timing is invalid: step "%s" starts at %.6fs before the previous step ended at %.6fs.', ...
            string(sequenceName), ...
            patternName, ...
            step.start_s, ...
            cursorTime ...
        );
    end
    gapDuration = step.start_s - cursorTime;
    if gapDuration > 1e-9
        hGroup.add(makePauseRoi([0 0], [0 0], gapDuration, nBeams));
    end
    appendPatternBlockToGroup(hGroup, pattern, patternNumber, hSI, opts, nBeams);
    cursorTime = step.start_s + pattern.duration_s;
end

hGroup.add(makeParkRoi(opts.ParkDuration, nBeams));
end


function appendPatternBlockToGroup(hGroup, pattern, patternNumber, hSI, opts, nBeams)
validateattributes(pattern.frequency_hz, {'numeric'}, {'scalar','positive','finite','nonnan'});
validateattributes(pattern.duty_cycle, {'numeric'}, {'scalar','finite','nonnan','>=',0,'<=',1});
validateattributes(pattern.power_percent, {'numeric'}, {'scalar','finite','nonnan','>=',0});
assert(isfield(pattern, 'cells') && ~isempty(pattern.cells), 'Pattern P%d contains no cells.', patternNumber);

[resX, resY] = getResolutionXY(hSI);
pointsUm = zeros(numel(pattern.cells), 4);
for i = 1:numel(pattern.cells)
    c = pattern.cells(i);
    cellLabel = getfieldwithdefault(c, 'label', sprintf('cell%d', i)); %#ok<GFLD>
    try
        x = getScalarField(c, 'x');
        y = getScalarField(c, 'y');
        z = getScalarField(c, 'z');
        powerScale = getScalarField(c, 'power_scale', 1);
    catch ME
        error('Pattern P%d cell "%s" is invalid: %s', patternNumber, string(cellLabel), ME.message);
    end
    pointsUm(i,:) = [x y z powerScale];
end

weights = pointsUm(:,4);
if ~any(weights > 0)
    weights = ones(size(weights));
end

centerUm = chooseSpiralCenter(pointsUm(:,1:2), weights, opts.MinCenterDistanceUm);
pointsRef = pointsUm;
pointsRef(:,1) = pointsUm(:,1) ./ resX;
pointsRef(:,2) = pointsUm(:,2) ./ resY;
centerRef = [centerUm(1) ./ resX, centerUm(2) ./ resY];
centerRef = reshape(centerRef, 1, []);

stimDuration = pattern.duty_cycle ./ pattern.frequency_hz;
repeatCount = max(1, round(pattern.duration_s .* pattern.frequency_hz));
cycleDuration = pattern.duration_s ./ repeatCount;
interStimPauseDuration = cycleDuration - opts.PreStimPauseDuration - stimDuration;
if interStimPauseDuration < -1e-9
    error( ...
        'Pattern P%d timing is impossible: duration %.6fs, frequency %.6fHz, duty_cycle %.6f, and pre-stim pause %.6fs exceed the cycle duration %.6fs.', ...
        patternNumber, ...
        pattern.duration_s, ...
        pattern.frequency_hz, ...
        pattern.duty_cycle, ...
        opts.PreStimPauseDuration, ...
        cycleDuration ...
    );
end
interStimPauseDuration = max(0, interStimPauseDuration);
spiralWidth = getfieldwithdefault(pattern, 'spiral_width', 10); %#ok<GFLD>
spiralHeight = getfieldwithdefault(pattern, 'spiral_height', 10); %#ok<GFLD>
sizeRef = [double(spiralWidth) ./ resX, double(spiralHeight) ./ resY];

stimField = scanimage.mroi.scanfield.fields.StimulusField();
stimField.centerXY = centerRef;
stimField.sizeXY = sizeRef;
stimField.duration = stimDuration;
stimField.repetitions = 1;
stimField.stimfcnhdl = @scanimage.mroi.stimulusfunctions.logspiral;
stimField.stimparams = {'revolutions', opts.Revolutions, 'direction', 'outward'};
nPoints = size(pointsRef, 1);
slmPattern = zeros(nPoints, 4);
slmPattern(:,1) = pointsRef(:,1) - centerRef(1);
slmPattern(:,2) = pointsRef(:,2) - centerRef(2);
slmPattern(:,3) = pointsRef(:,3);
slmPattern(:,4) = pointsRef(:,4);
stimField.slmPattern = slmPattern;
if ismethod(stimField, 'recenterGalvoOntoSlmPattern')
    stimField.recenterGalvoOntoSlmPattern();
end

beamPowers = zeros(1, nBeams);
beamPowers(3) = pattern.power_percent;
stimField.powers = beamPowers;
stimField.duration = cycleDuration;
stimField.repetitions = repeatCount;
stimField.stimfcnhdl = @opto.scanimage.pulsedStimulusPath;
stimField.stimparams = { ...
    'prePause_s', opts.PreStimPauseDuration, ...
    'stimActive_s', stimDuration, ...
    'delegateFunction', 'scanimage.mroi.stimulusfunctions.logspiral', ...
    'delegateParams', {'revolutions', opts.Revolutions, 'direction', 'outward'} ...
};
stimField.beamsfcnhdl = @opto.scanimage.pulsedBeamPowers;
hGroup.add(makeStimRoi(stimField));
end


function group = makeBlankOnlyGroup(name, durationSeconds, nBeams)
group = scanimage.mroi.RoiGroup(char(name));
group.add(makeParkRoi(durationSeconds, nBeams));
end


function group = makeParkOnlyGroup(name, durationSeconds, nBeams)
group = scanimage.mroi.RoiGroup(char(name));
group.add(makeParkRoi(durationSeconds, nBeams));
end


function roi = makePauseRoi(centerXY, sizeXY, durationSeconds, nBeams)
sfPause = scanimage.mroi.scanfield.fields.StimulusField();
sfPause.centerXY = centerXY;
sfPause.sizeXY = sizeXY;
sfPause.stimfcnhdl = @scanimage.mroi.stimulusfunctions.pause;
sfPause.stimparams = {'poweredPause', false};
sfPause.duration = durationSeconds;
sfPause.repetitions = 1;
sfPause.powers = zeros(1, nBeams);
roi = scanimage.mroi.Roi();
roi.add(0, sfPause);
end


function roi = makeStimRoi(stimField)
roi = scanimage.mroi.Roi();
roi.add(0, stimField.copy());
end


function roi = makeParkRoi(durationSeconds, nBeams)
sfPause = scanimage.mroi.scanfield.fields.StimulusField();
sfPause.centerXY = [0 0];
sfPause.sizeXY = [0 0];
sfPause.stimfcnhdl = @scanimage.mroi.stimulusfunctions.park;
sfPause.stimparams = {};
sfPause.duration = durationSeconds;
sfPause.repetitions = 1;
sfPause.powers = zeros(1, nBeams);
roi = scanimage.mroi.Roi();
roi.add(0, sfPause);
end


function [resX, resY] = getResolutionXY(hSI)
assert(~isempty(hSI.objectiveResolution), 'objectiveResolution is not set in ScanImage.');
res = hSI.objectiveResolution;
if isscalar(res)
    resX = res;
    resY = res;
else
    assert(numel(res) >= 2, 'objectiveResolution must be scalar or 2-element.');
    resX = res(1);
    resY = res(2);
end
end


function center = chooseSpiralCenter(pointsXY, weights, minDistanceUm)
weights = weights(:);
if ~any(weights > 0)
    weights = ones(size(weights));
end
center0 = sum(pointsXY .* weights, 1) ./ sum(weights);
if all(vecnorm(pointsXY - center0, 2, 2) >= minDistanceUm)
    center = center0;
    return;
end

angles = linspace(0, 2*pi, 361);
maxRadius = max([minDistanceUm * 4, max(vecnorm(pointsXY - center0, 2, 2)) + minDistanceUm * 2, 50]);
radii = linspace(0, maxRadius, 801);
for r = radii
    for a = angles
        candidate = center0 + r * [cos(a) sin(a)];
        if all(vecnorm(pointsXY - candidate, 2, 2) >= minDistanceUm)
            center = candidate;
            return;
        end
    end
end

center = center0 + [minDistanceUm 0];
end


function value = getfieldwithdefault(s, fieldName, defaultValue)
if isfield(s, fieldName) && ~isempty(s.(fieldName))
    value = s.(fieldName);
else
    value = defaultValue;
end
end


function value = getScalarField(s, fieldName, defaultValue)
if nargin < 3
    hasDefault = false;
    defaultValue = [];
else
    hasDefault = true;
end
if ~isfield(s, fieldName) || isempty(s.(fieldName))
    if hasDefault
        value = double(defaultValue);
        return;
    end
    error('Missing required field "%s".', fieldName);
end
raw = s.(fieldName);
if ischar(raw) || isstring(raw)
    raw = str2double(string(raw));
end
raw = double(raw);
if numel(raw) ~= 1 || ~isfinite(raw)
    error('Field "%s" must be a finite scalar.', fieldName);
end
value = raw;
end


function triggerTerm = normalizePhotostimTriggerTerm(triggerTerm)
triggerTerm = strtrim(triggerTerm);
if startsWith(triggerTerm, '/')
    parts = regexp(triggerTerm, '/([^/]+)$', 'tokens', 'once');
    if ~isempty(parts)
        triggerTerm = parts{1};
    end
end
end


function nBeams = getPhotostimBeamCount(hSI)
ss = hPsStimScannerset(hSI);
nBeams = 1;
try
    if most.idioms.isValidObj(ss)
        nBeams = numel(ss.beams);
    end
catch
    nBeams = 1;
end
assert(nBeams >= 3, 'Photostim expects at least 3 beams; only %d configured.', nBeams);
end


function ss = hPsStimScannerset(hSI)
ss = hSI.hPhotostim.stimScannerset;
end
