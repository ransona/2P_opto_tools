function meanTiffPath = acquirePostBurnMeanTiff(hSI, outputDir, varargin)
%ACQUIREPOSTBURNMEANTIFF Acquire post-stim frames and save their mean image.

parser = inputParser();
parser.addRequired('hSI');
parser.addRequired('outputDir', @(x) ischar(x) || isstring(x));
parser.addParameter('NumFrames', 100, @(x) isnumeric(x) && isscalar(x) && x >= 1);
parser.addParameter('TimeoutSeconds', 30, @(x) isnumeric(x) && isscalar(x) && x > 0);
parser.addParameter('LogStem', 'post_burn', @(x) ischar(x) || isstring(x));
parser.parse(hSI, outputDir, varargin{:});

outputDir = char(outputDir);
if exist(outputDir, 'dir') ~= 7
    mkdir(outputDir);
end

nFrames = max(1, round(parser.Results.NumFrames));
logStem = char(parser.Results.LogStem);
timeoutSeconds = double(parser.Results.TimeoutSeconds);

assert(isValidObj(hSI), 'ScanImage is not running.');
assert(isprop(hSI, 'hScan2D') && isValidObj(hSI.hScan2D), 'Scan2D component is not available.');
assert(isprop(hSI, 'hChannels') && isValidObj(hSI.hChannels), 'Channels component is not available.');
assert(~isScanImageActive(hSI), 'Cannot acquire post-burn mean while ScanImage acquisition is already active.');

waitForPhotostimIdle(hSI, 5);
preparePhotostimForPostBurnGrab(hSI);

cfg = configureGrab(hSI, outputDir, logStem, nFrames);
cleanupObj = onCleanup(@() restoreGrabConfig(hSI, cfg));

waitForScanImageInactive(hSI, timeoutSeconds);
rawTiffPath = resolveLoggedTiffPath(cfg);
assert(~isempty(rawTiffPath), 'Post-burn acquisition finished but no TIFF was found.');

meanImg = computeMeanImage(rawTiffPath, nFrames);
assert(~isempty(meanImg), 'Could not compute post-burn mean image from %s.', rawTiffPath);

meanTiffPath = fullfile(outputDir, [logStem '_mean.tif']);
imwrite(meanImg, meanTiffPath);
end


function cfg = configureGrab(hSI, outputDir, logStem, nFrames)
hScan2D = hSI.hScan2D;
hChannels = hSI.hChannels;

cfg = struct();
cfg.logFilePath = outputDir;
cfg.logFileStem = logStem;
cfg.tStartDatenum = now;
cfg.oldChannelSave = hChannels.channelSave;
cfg.oldLoggingEnable = hChannels.loggingEnable;
cfg.oldLogFileStem = hScan2D.logFileStem;
cfg.oldLogFilePath = hScan2D.logFilePath;
cfg.oldFramesPerAcq = hScan2D.framesPerAcq;
cfg.oldLogFramesPerFile = hScan2D.logFramesPerFile;
cfg.oldStackEnable = [];
cfg.oldStackFramesPerSlice = [];
cfg.usedStackFramesFallback = false;

try
    if isprop(hSI, 'hStackManager') && isValidObj(hSI.hStackManager)
        cfg.oldStackEnable = hSI.hStackManager.enable;
        cfg.oldStackFramesPerSlice = hSI.hStackManager.framesPerSlice;
    end
catch
end

try
    hChannels.loggingEnable = true;
    if isempty(hChannels.channelSave)
        hChannels.channelSave = resolveGreenChannel(hSI);
    end
    hScan2D.logFilePath = outputDir;
    hScan2D.logFileStem = logStem;
    hScan2D.logFramesPerFile = inf;
    try
        hScan2D.framesPerAcq = nFrames;
    catch MEsetFrames
        if isprop(hSI, 'hStackManager') && isValidObj(hSI.hStackManager)
            cfg.usedStackFramesFallback = true;
            hSI.hStackManager.enable = false;
            hSI.hStackManager.framesPerSlice = nFrames;
        else
            rethrow(MEsetFrames);
        end
    end
    hSI.startGrab();
catch ME
    restoreGrabConfig(hSI, cfg);
    error('Failed to start post-burn 100-frame GRAB: %s', ME.message);
end
end


function restoreGrabConfig(hSI, cfg)
if ~isstruct(cfg) || ~isValidObj(hSI) || ~isprop(hSI, 'hScan2D') || ~isValidObj(hSI.hScan2D)
    return;
end
if isScanImageActive(hSI)
    return;
end

try
    if isprop(hSI, 'hChannels') && isValidObj(hSI.hChannels)
        hSI.hChannels.channelSave = cfg.oldChannelSave;
        hSI.hChannels.loggingEnable = cfg.oldLoggingEnable;
    end
catch
end
try
    hSI.hScan2D.logFileStem = cfg.oldLogFileStem;
catch
end
try
    hSI.hScan2D.logFilePath = cfg.oldLogFilePath;
catch
end
try
    hSI.hScan2D.framesPerAcq = cfg.oldFramesPerAcq;
catch
end
try
    hSI.hScan2D.logFramesPerFile = cfg.oldLogFramesPerFile;
catch
end
try
    if isprop(hSI, 'hStackManager') && isValidObj(hSI.hStackManager)
        if ~isempty(cfg.oldStackFramesPerSlice)
            hSI.hStackManager.framesPerSlice = cfg.oldStackFramesPerSlice;
        end
        if ~isempty(cfg.oldStackEnable)
            hSI.hStackManager.enable = logical(cfg.oldStackEnable);
        end
    end
catch
end
end


function waitForPhotostimIdle(hSI, timeoutSeconds)
if ~isprop(hSI, 'hPhotostim') || ~isValidObj(hSI.hPhotostim)
    return;
end
t0 = tic;
while toc(t0) < timeoutSeconds
    try
        if ~hSI.hPhotostim.active
            return;
        end
    catch
        return;
    end
    pause(0.05);
    drawnow();
end
error('Photostim was still active %.1f seconds after the burn pass.', timeoutSeconds);
end


function preparePhotostimForPostBurnGrab(hSI)
if ~isprop(hSI, 'hPhotostim') || ~isValidObj(hSI.hPhotostim)
    return;
end

hPhotostim = hSI.hPhotostim;
try
    if isprop(hPhotostim, 'monitoring')
        hPhotostim.monitoring = false;
    end
catch ME
    suppressKnownPhotostimControlsError(ME);
end
try
    if isprop(hPhotostim, 'logging')
        hPhotostim.logging = false;
    end
catch ME
    suppressKnownPhotostimControlsError(ME);
end
try
    hPhotostim.stimRoiGroups = [];
catch ME
    suppressKnownPhotostimControlsError(ME);
end
end


function waitForScanImageInactive(hSI, timeoutSeconds)
t0 = tic;
while toc(t0) < timeoutSeconds
    if ~isScanImageActive(hSI)
        return;
    end
    try
        pause(0.05);
        drawnow();
    catch ME
        suppressKnownPhotostimControlsError(ME);
    end
end
error('Post-burn GRAB did not finish within %.1f seconds.', timeoutSeconds);
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


function tiffPath = resolveLoggedTiffPath(cfg)
tiffPath = '';
pattern = fullfile(cfg.logFilePath, sprintf('%s*.tif', cfg.logFileStem));
t0 = tic;
while toc(t0) < 4
    files = dir(pattern);
    if ~isempty(files)
        files = files([files.datenum] >= (cfg.tStartDatenum - 2 / 86400));
    end
    if ~isempty(files)
        [~, idx] = max([files.datenum]);
        tiffPath = fullfile(files(idx).folder, files(idx).name);
        return;
    end
    pause(0.1);
end
end


function meanImg = computeMeanImage(tiffPath, nFrames)
meanImg = [];
info = imfinfo(tiffPath);
nUse = min(nFrames, numel(info));
if nUse < 1
    return;
end

acc = [];
nAccum = 0;
for idx = 1:nUse
    frame = imread(tiffPath, idx, 'Info', info);
    if isempty(acc)
        acc = zeros(size(frame), 'double');
    elseif ~isequal(size(frame), size(acc))
        continue;
    end
    acc = acc + double(frame);
    nAccum = nAccum + 1;
end

if nAccum < 1
    return;
end
meanDouble = acc ./ nAccum;
meanImg = uint16(max(0, min(double(intmax('uint16')), round(meanDouble))));
end


function ch = resolveGreenChannel(hSI)
ch = [];
try
    mergeColors = hSI.hChannels.channelMergeColor;
    if ischar(mergeColors)
        mergeColors = {mergeColors};
    end
    idx = find(strcmpi(mergeColors, 'green'), 1, 'first');
    if ~isempty(idx)
        ch = idx;
    end
catch
    ch = [];
end
if isempty(ch)
    try
        if ~isempty(hSI.hChannels.channelSave)
            ch = hSI.hChannels.channelSave(1);
        end
    catch
        ch = [];
    end
end
if isempty(ch)
    ch = 1;
end
ch = round(ch(1));
end


function tf = isScanImageActive(hSI)
tf = false;
try
    tf = ~isempty(hSI.active) && logical(hSI.active);
catch
end
if ~tf
    try
        tf = any(strcmpi(char(hSI.acqState), {'focus', 'grab', 'loop', 'loop_wait'}));
    catch
    end
end
end


function tf = isValidObj(obj)
try
    tf = ~isempty(obj) && isvalid(obj);
catch
    tf = ~isempty(obj);
end
end
