function triggerPhotostimPattern(hSI, patternRoiPath, varargin)
%TRIGGERPHOTOSTIMPATTERN Load a stimulus ROI group and trigger photostim once.
%   TRIGGERPHOTOSTIMPATTERN(HSI, PATTERNROIPATH) loads the ROI group from file,
%   assigns it to hSI.hPhotostim.stimRoiGroups, starts photostim if needed,
%   and triggers the first stimulus group.
%
%   Name/value pairs:
%       'StimGroupIndex'  Stim group index for on-demand mode. Default: 1
%       'UseOnDemand'     True to force onDemandStimNow(idx), false to force
%                         triggerStim(), or 'auto' to follow the active
%                         Photostim stimulusMode. Default: 'auto'

parser = inputParser();
parser.addRequired('hSI');
parser.addRequired('patternRoiPath', @(x) ischar(x) || isstring(x));
parser.addParameter('StimGroupIndex', 1, @(x) isnumeric(x) && isscalar(x) && x >= 1);
parser.addParameter('UseOnDemand', 'auto', @(x) islogical(x) || isnumeric(x) || ischar(x) || isstring(x));
parser.addParameter('PrimeSettleSeconds', 0.1, @(x) isnumeric(x) && isscalar(x) && x >= 0);
parser.parse(hSI, patternRoiPath, varargin{:});

assert(isprop(hSI, 'hPhotostim') && ~isempty(hSI.hPhotostim), ...
    'Provided hSI does not expose hPhotostim.');

ensureBundledScanImageOnPath();
roiGroup = scanimage.mroi.RoiGroup.loadFromFile(char(patternRoiPath));
hPhotostim = hSI.hPhotostim;
ensurePhotostimIdle(hSI, hPhotostim);
setStimRoiGroupsSuppressingKnownGuiError(hSI, hPhotostim, roiGroup);
useOnDemand = resolveUseOnDemand(hPhotostim, parser.Results.UseOnDemand);

if ~useOnDemand
    configureSequenceTrigger(hSI, hPhotostim);
end

if ~hPhotostim.active
    if useOnDemand && ~strcmp(hPhotostim.stimulusMode, 'onDemand')
        setPhotostimPropertySuppressingKnownGuiError(hSI, hPhotostim, 'stimulusMode', 'onDemand');
    end
    callSuppressingKnownGuiError(hSI, @() hPhotostim.start());
    assert(hPhotostim.active, ...
        ['Photostim did not become active after start(). Check the preceding ' ...
         'ScanImage ErrorHandler message for the startup failure.']);
    assertPhotostimPrimed(hPhotostim, roiGroup);
end
pause(parser.Results.PrimeSettleSeconds);
drawnow();

if useOnDemand
    callSuppressingKnownGuiError(hSI, @() hPhotostim.onDemandStimNow(parser.Results.StimGroupIndex));
else
    callSuppressingKnownGuiError(hSI, @() hPhotostim.triggerStim());
end
waitThenAbortPhotostim(hSI, hPhotostim, 5);
end


function ensurePhotostimIdle(hSI, hPhotostim)
if ~isPhotostimActive(hPhotostim)
    return;
end
callSuppressingKnownGuiError(hSI, @() hPhotostim.abort());
t0 = tic;
while toc(t0) < 5
    if ~isPhotostimActive(hPhotostim)
        return;
    end
    pause(0.05);
    drawnow();
end
error('Photostim remained active after abort; cannot safely configure the next burn stimulus.');
end


function waitThenAbortPhotostim(hSI, hPhotostim, timeoutSeconds)
t0 = tic;
while toc(t0) < timeoutSeconds
    if ~isPhotostimActive(hPhotostim)
        return;
    end
    pause(0.02);
    drawnow();
end
callSuppressingKnownGuiError(hSI, @() hPhotostim.abort());
end


function tf = isPhotostimActive(hPhotostim)
tf = false;
try
    tf = isvalid(hPhotostim) && ~isempty(hPhotostim.active) && logical(hPhotostim.active);
catch
end
end


function setStimRoiGroupsSuppressingKnownGuiError(hSI, hPhotostim, roiGroup)
try
    stabilizePhotostimControlsSelection(hSI);
    hPhotostim.stimRoiGroups = roiGroup;
catch ME
    suppressKnownPhotostimControlsError(ME);
end
end


function setPhotostimPropertySuppressingKnownGuiError(hSI, hPhotostim, propertyName, value)
try
    stabilizePhotostimControlsSelection(hSI);
    hPhotostim.(propertyName) = value;
catch ME
    suppressKnownPhotostimControlsError(ME);
end
end


function callSuppressingKnownGuiError(hSI, fcn)
try
    stabilizePhotostimControlsSelection(hSI);
    fcn();
catch ME
    suppressKnownPhotostimControlsError(ME);
end
end


function configureSequenceTrigger(hSI, hPhotostim)
if hPhotostim.active && ~strcmp(hPhotostim.stimulusMode, 'sequence')
    error(['Photostim is active in stimulusMode=''%s''. triggerStim requires ' ...
        'sequence mode for this burn path. Stop Photostim and retry.'], ...
        hPhotostim.stimulusMode);
end

if ~strcmp(hPhotostim.stimulusMode, 'sequence')
    setPhotostimPropertySuppressingKnownGuiError(hSI, hPhotostim, 'stimulusMode', 'sequence');
end
setPhotostimPropertySuppressingKnownGuiError(hSI, hPhotostim, 'sequenceSelectedStimuli', 1);
setPhotostimPropertySuppressingKnownGuiError(hSI, hPhotostim, 'numSequences', 1);
setPhotostimPropertySuppressingKnownGuiError(hSI, hPhotostim, 'autoTriggerPeriod', 0);
setPhotostimPropertySuppressingKnownGuiError(hSI, hPhotostim, 'stimImmediately', false);
end


function stabilizePhotostimControlsSelection(hSI)
controllers = {};
try
    if isprop(hSI, 'hController') && ~isempty(hSI.hController)
        controllers = hSI.hController;
    end
catch
    controllers = {};
end
if ~iscell(controllers)
    controllers = num2cell(controllers);
end

for controllerIndex = 1:numel(controllers)
    hController = controllers{controllerIndex};
    try
        if isempty(hController) || ~isvalid(hController) || ~isprop(hController, 'hPhotostimControls')
            continue;
        end
        hPhotostimControls = hController.hPhotostimControls;
        if isempty(hPhotostimControls) || ~isvalid(hPhotostimControls)
            continue;
        end
        hPhotostimControls.currentlySelectedCell = 1;
        try
            hPhotostimControls.stimGroupsTable.Value = [];
        catch
        end
    catch
    end
end
end


function assertPhotostimPrimed(hPhotostim, roiGroup)
try
    if isprop(hPhotostim, 'hasSlm') && hPhotostim.hasSlm ...
            && isprop(hPhotostim, 'currentSlmPattern')
        assert(~isempty(hPhotostim.currentSlmPattern), ...
            ['Photostim started, but no SLM pattern was primed. This usually means ' ...
             'a ScanImage GUI listener interrupted Photostim.start() before the phase mask write.']);
    end
catch ME
    if strcmp(ME.identifier, 'MATLAB:assertion:failed')
        rethrow(ME);
    end
end

try
    assert(~isempty(roiGroup) && numel(roiGroup.rois) >= 1, ...
        'Loaded photostim ROI group is empty.');
catch ME
    if strcmp(ME.identifier, 'MATLAB:assertion:failed')
        rethrow(ME);
    end
end
end


function suppressKnownPhotostimControlsError(ME)
if ~isKnownPhotostimControlsIndexError(ME)
    rethrow(ME);
end
end


function tf = isKnownPhotostimControlsIndexError(ME)
tf = exceptionHasMessage(ME, 'Array indices must be positive integers or logical values') ...
    && exceptionHasStackFrame(ME, 'highlightTableCells');
end


function tf = exceptionHasMessage(ME, pattern)
tf = contains(ME.message, pattern);
for causeIndex = 1:numel(ME.cause)
    tf = tf || exceptionHasMessage(ME.cause{causeIndex}, pattern);
end
end


function tf = exceptionHasStackFrame(ME, pattern)
tf = ~isempty(ME.stack) && any(contains({ME.stack.name}, pattern));
for causeIndex = 1:numel(ME.cause)
    tf = tf || exceptionHasStackFrame(ME.cause{causeIndex}, pattern);
end
end


function useOnDemand = resolveUseOnDemand(hPhotostim, requestedMode)
if ischar(requestedMode) || isstring(requestedMode)
    requestedMode = char(requestedMode);
    assert(strcmpi(requestedMode, 'auto'), ...
        'UseOnDemand must be true, false, or ''auto''.');
    useOnDemand = ~hPhotostim.active || strcmp(hPhotostim.stimulusMode, 'onDemand');
else
    useOnDemand = logical(requestedMode);
end

if useOnDemand && hPhotostim.active && ~strcmp(hPhotostim.stimulusMode, 'onDemand')
    error(['Photostim is active in stimulusMode=''%s''. onDemandStimNow can only be ' ...
        'used in onDemand mode. Stop Photostim or call with UseOnDemand=false.'], ...
        hPhotostim.stimulusMode);
end
end
