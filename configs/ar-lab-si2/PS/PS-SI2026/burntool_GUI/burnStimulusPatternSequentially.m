function tiffPath = burnStimulusPatternSequentially(patternRoiPath, varargin)
%BURNSTIMULUSPATTERNSEQUENTIALLY Burn each stimulus target one after another.
%   TIFFPATH = BURNSTIMULUSPATTERNSEQUENTIALLY(PATTERNROIPATH, ...) iterates over
%   the target entries in PATTERNROIPATH, builds a single-target stimulus group for
%   each one, invokes a user-supplied burn callback, then invokes a user-supplied
%   acquisition callback after the pass and returns its TIFF path.
%
%   Required name/value pairs:
%       'BurnSingleHoleFcn'   @(singleRoiPath, singleRoiGroup, holeState) ...
%       'AcquireAfterPassFcn' @(passState) tiffPath
%
%   Optional name/value pairs:
%       'AcquireBeforePassFcn' @(passState) tiffPath
%       'OutputDir'           Directory for temporary single-hole ROI files
%       'RoiIndices'          Subset of target indices to burn. Default: all
%       'PauseSeconds'        Pause after each hole burn. Default: 0
%       'ProgressFcn'         @(eventName, eventData) ...
%       'StopCheckFcn'        @() logical scalar
%       'State'               Struct passed through to callbacks

parser = inputParser();
parser.addRequired('patternRoiPath', @(x) ischar(x) || isstring(x));
parser.addParameter('BurnSingleHoleFcn', [], @(x) isa(x, 'function_handle'));
parser.addParameter('AcquireBeforePassFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('AcquireAfterPassFcn', [], @(x) isa(x, 'function_handle'));
parser.addParameter('OutputDir', '', @(x) ischar(x) || isstring(x));
parser.addParameter('RoiIndices', [], @(x) isnumeric(x) && isvector(x));
parser.addParameter('PauseSeconds', 0, @(x) isnumeric(x) && isscalar(x) && x >= 0);
parser.addParameter('ProgressFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('StopCheckFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('State', struct(), @isstruct);
parser.parse(patternRoiPath, varargin{:});

opts = parser.Results;
patternRoiPath = char(patternRoiPath);
ensureBundledScanImageOnPath();
sourceGroup = scanimage.mroi.RoiGroup.loadFromFile(patternRoiPath);
targets = enumerateStimulusTargets(sourceGroup);

if isempty(opts.RoiIndices)
    roiIndices = 1:numel(targets);
else
    roiIndices = reshape(double(opts.RoiIndices), 1, []);
end

if isempty(opts.OutputDir)
    if isfield(opts.State, 'iterationDir') && ~isempty(opts.State.iterationDir)
        opts.OutputDir = fullfile(opts.State.iterationDir, 'sequential_burn');
    else
        opts.OutputDir = fullfile(pwd, 'sequential_burn');
    end
end
outputDir = char(opts.OutputDir);
if exist(outputDir, 'dir') ~= 7
    mkdir(outputDir);
end

emitProgress(opts.ProgressFcn, 'startPass', struct( ...
    'patternRoiPath', patternRoiPath, ...
    'roiIndices', roiIndices, ...
    'outputDir', outputDir, ...
    'state', opts.State));

passState = opts.State;
passState.patternRoiPath = patternRoiPath;
passState.roiIndices = roiIndices;
passState.outputDir = outputDir;
passState.preBurnTiffPath = '';
if ~isempty(opts.AcquireBeforePassFcn)
    emitProgress(opts.ProgressFcn, 'acquireBeforePass', passState);
    passState.preBurnTiffPath = char(opts.AcquireBeforePassFcn(passState));
    emitProgress(opts.ProgressFcn, 'beforePassAcquired', passState);
end

for positionIndex = 1:numel(roiIndices)
    if shouldStop(opts.StopCheckFcn)
        error('burnStimulusPatternSequentially:Stopped', 'Sequential burn stopped by user request.');
    end

    roiIndex = roiIndices(positionIndex);
    singleRoiGroup = buildSingleStimulusRoiGroup(sourceGroup, roiIndex);
    singleRoiPath = fullfile(outputDir, sprintf('hole_%03d.roi', roiIndex));
    singleRoiGroup.saveToFile(singleRoiPath);

    holeState = opts.State;
    holeState.roiIndex = roiIndex;
    holeState.positionIndex = positionIndex;
    holeState.numHoles = numel(roiIndices);
    holeState.singleRoiPath = singleRoiPath;
    holeState.outputDir = outputDir;
    holeState.preBurnTiffPath = passState.preBurnTiffPath;

    emitProgress(opts.ProgressFcn, 'startHole', holeState);
    opts.BurnSingleHoleFcn(singleRoiPath, singleRoiGroup, holeState);
    emitProgress(opts.ProgressFcn, 'endHole', holeState);

    if opts.PauseSeconds > 0
        pause(opts.PauseSeconds);
    end
end

emitProgress(opts.ProgressFcn, 'acquireAfterPass', passState);
tiffPath = char(opts.AcquireAfterPassFcn(passState));
passState.postBurnTiffPath = tiffPath;
emitProgress(opts.ProgressFcn, 'endPass', struct('tiffPath', tiffPath, 'state', passState));
end


function emitProgress(progressFcn, eventName, eventData)
if ~isempty(progressFcn)
    progressFcn(eventName, eventData);
end
end


function tf = shouldStop(stopCheckFcn)
tf = false;
if ~isempty(stopCheckFcn)
    tf = logical(stopCheckFcn());
end
end
