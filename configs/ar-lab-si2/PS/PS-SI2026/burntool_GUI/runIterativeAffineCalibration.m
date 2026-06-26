function result = runIterativeAffineCalibration(patternRoiPath, varargin)
%RUNITERATIVEAFFINECALIBRATION Iteratively refine photostim alignment from burned holes.
%   RESULT = RUNITERATIVEAFFINECALIBRATION(PATTERNROIPATH, ...) runs a closed-loop
%   calibration cycle:
%       1) apply the current scanner->reference transform
%       2) burn the requested stimulus pattern
%       3) acquire/detect the achieved hole centers
%       4) fit an affine correction in reference space
%       5) update the transform and repeat until no longer improving
%
%   The function is intentionally split into a concrete affine/matching engine and
%   explicit hardware hooks. The repo already contains the transform math, but the
%   exact burn/acquire path is microscope-specific, so those actions are supplied via
%   callbacks in OPTIONS.
%
%   Required name/value pairs:
%       'InitialTransform'    3x3 scanner->reference affine matrix.
%
%   Common optional name/value pairs:
%       'OutputDir'           Directory for iteration outputs. Default:
%                             <pwd>\iterative_affine_calibration_<timestamp>
%       'MaxIterations'       Maximum refinement rounds. Default: 5
%       'MinImprovementUm'    Minimum confirmed RMS improvement in native
%                             reference units to continue. Default: 0.05
%       'MinPredictedImprovementUm'
%                             Deprecated compatibility option. Candidate
%                             transforms are selected only after measured burns.
%       'MaxMatchDistanceUm'  Max target-achieved match distance. Default: inf
%       'MinMatchedPoints'    Minimum matches needed to fit. Default: 3
%       'UseFullAffine'       True for 6-DOF affine, false for translation only.
%                             Default: true
%       'UseScaleCorrection'  With UseFullAffine=false, allow independent X/Y
%                             scale plus translation. Default: false
%       'ApplyTransformFcn'   @(transformMatrix, state) ...
%       'BurnAndAcquireFcn'   @(iterStimRoiPath, iterationState) tiffPath
%       'DetectHolesFcn'      @(tiffPath, iterationState) achievedRefXY
%       'MoveAfterIterationFcn' @(iterationState) ...
%       'LoadStimGroupFcn'    @(roiPath, iterationState) roiGroup
%       'SaveTransformFcn'    @(transformMatrix, path, state) ...
%       'StopOnNonImprovement' true/false. Default: true
%
%   RESULT contains:
%       .bestTransform
%       .bestIteration
%       .iterations
%       .outputDir
%       .requestedRefXY
%       .scannerXY
%
%   Example:
%       opts = struct();
%       opts.InitialTransform = load('GGtoP1_working.mat').GGtoP1_working;
%       opts.ApplyTransformFcn = @(T,s) setPhotostimScannerTransform(s.hSI, T);
%       opts.BurnAndAcquireFcn = @(roiPath,s) burnPatternAndAcquireTiff(s.hSI, roiPath);
%       opts.DetectHolesFcn = @(tiffPath,s) detectHolesFromTiffCsv(tiffPath);
%       result = runIterativeAffineCalibration('affine_test_pattern.roi', opts);

if isscalar(varargin) && isstruct(varargin{1})
    varargin = namedArgsFromStruct(varargin{1});
end

parser = inputParser();
parser.addRequired('patternRoiPath', @(x) ischar(x) || isstring(x));
parser.addParameter('InitialTransform', [], @(x) isnumeric(x) && isequal(size(x), [3 3]));
parser.addParameter('OutputDir', '', @(x) ischar(x) || isstring(x));
parser.addParameter('MaxIterations', 5, @(x) isnumeric(x) && isscalar(x) && x >= 1);
parser.addParameter('MinImprovementUm', 0.05, @(x) isnumeric(x) && isscalar(x) && x >= 0);
parser.addParameter('MinPredictedImprovementUm', 0, @(x) isnumeric(x) && isscalar(x) && x >= 0);
parser.addParameter('MaxMatchDistanceUm', inf, @(x) isnumeric(x) && isscalar(x) && x > 0);
parser.addParameter('MinMatchedPoints', 3, @(x) isnumeric(x) && isscalar(x) && x >= 2);
parser.addParameter('UseFullAffine', true, @(x) islogical(x) || isnumeric(x));
parser.addParameter('UseScaleCorrection', false, @(x) islogical(x) || isnumeric(x));
parser.addParameter('StopOnNonImprovement', true, @(x) islogical(x) || isnumeric(x));
parser.addParameter('ApplyTransformFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('BurnAndAcquireFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('DetectHolesFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('MoveAfterIterationFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('LoadStimGroupFcn', @defaultLoadStimGroup, @(x) isa(x, 'function_handle'));
parser.addParameter('SaveTransformFcn', @defaultSaveTransform, @(x) isa(x, 'function_handle'));
parser.addParameter('State', struct(), @isstruct);
parser.parse(patternRoiPath, varargin{:});

opts = parser.Results;
patternRoiPath = char(patternRoiPath);
assert(exist(patternRoiPath, 'file') == 2, 'Pattern ROI file not found: %s', patternRoiPath);

if isempty(opts.OutputDir)
    timestamp = char(datetime('now', 'Format', 'yyyyMMdd_HHmmss'));
    opts.OutputDir = fullfile(pwd, ['iterative_affine_calibration_' timestamp]);
end
outputDir = char(opts.OutputDir);
if exist(outputDir, 'dir') ~= 7
    mkdir(outputDir);
end

if isempty(opts.ApplyTransformFcn)
    opts.ApplyTransformFcn = @defaultApplyTransformError;
end
if isempty(opts.BurnAndAcquireFcn)
    opts.BurnAndAcquireFcn = @defaultBurnAndAcquireError;
end
if isempty(opts.DetectHolesFcn)
    opts.DetectHolesFcn = @defaultDetectHolesError;
end

stimRoiGroup = opts.LoadStimGroupFcn(patternRoiPath, opts.State);
requestedRefXY = extractStimulusCenters(stimRoiGroup);
assert(size(requestedRefXY, 1) >= opts.MinMatchedPoints, ...
    'Pattern ROI contains %d points, fewer than MinMatchedPoints=%d.', ...
    size(requestedRefXY, 1), opts.MinMatchedPoints);

currentTransform = double(opts.InitialTransform);

result = struct();
result.outputDir = outputDir;
result.patternRoiPath = patternRoiPath;
result.scannerXY = requestedRefXY;
result.requestedRefXY = requestedRefXY;
result.iterations = repmat(emptyIterationRecord(), 0, 1);
result.bestTransform = currentTransform;
result.bestIteration = 0;

bestMeasuredRmsUm = inf;
bestTransform = currentTransform;
stopRequested = false;
previousMeasuredRmsUm = [];

for iterationIndex = 1:opts.MaxIterations
    iterationDir = fullfile(outputDir, sprintf('iter_%02d', iterationIndex));
    if exist(iterationDir, 'dir') ~= 7
        mkdir(iterationDir);
    end

    iterState = opts.State;
    iterState.iteration = iterationIndex;
    iterState.iterationDir = iterationDir;
    iterState.currentTransform = currentTransform;
    currentPredictedRefXY = requestedRefXY;
    iterState.currentRequestedRefXY = currentPredictedRefXY;
    iterState.patternRoiPath = patternRoiPath;
    iterState.scannerXY = requestedRefXY;
    iterState.requestedRefXY = currentPredictedRefXY;

    opts.ApplyTransformFcn(currentTransform, iterState);
    opts.SaveTransformFcn(currentTransform, fullfile(iterationDir, 'transform_before_burn.mat'), iterState);

    iterStimRoiPath = fullfile(iterationDir, 'stim_pattern_used.roi');
    if ~strcmpi(patternRoiPath, iterStimRoiPath)
        copyfile(patternRoiPath, iterStimRoiPath);
    end

    tiffPath = opts.BurnAndAcquireFcn(iterStimRoiPath, iterState);
    achievedRefXY = double(opts.DetectHolesFcn(tiffPath, iterState));
    validateattributes(achievedRefXY, {'numeric'}, {'2d', 'ncols', 2, 'nonempty'}, ...
        mfilename, 'achievedRefXY');

    [matchPairs, matchedRequestedRefXY, matchedAchievedRefXY] = matchTargetAndAchievedPoints( ...
        currentPredictedRefXY, achievedRefXY, opts.MaxMatchDistanceUm);

    assert(size(matchPairs, 1) >= opts.MinMatchedPoints, ...
        ['Only %d matches found in iteration %d. Increase MaxMatchDistanceUm, ' ...
         'improve detection, or verify pattern ordering.'], ...
        size(matchPairs, 1), iterationIndex);

    correctionAchievedToRequested = fitAffineCorrection( ...
        matchedAchievedRefXY, matchedRequestedRefXY, ...
        logical(opts.UseFullAffine), logical(opts.UseScaleCorrection));
    % ScanImage applies scannerToRefTransform in the opposite direction from the
    % measured achieved->requested diagnostic correction.
    candidateTransform = currentTransform / correctionAchievedToRequested;
    correctedAchievedRefXY = applyAffineToPoints(matchedAchievedRefXY, correctionAchievedToRequested);

    measuredResidualUm = rowwiseDistance(matchedAchievedRefXY, matchedRequestedRefXY);
    predictedResidualAfterUpdateUm = rowwiseDistance(correctedAchievedRefXY, matchedRequestedRefXY);

    measuredRmsUm = rmsMagnitude(measuredResidualUm);
    predictedRmsAfterUpdateUm = rmsMagnitude(predictedResidualAfterUpdateUm);

    if isempty(previousMeasuredRmsUm)
        confirmedImprovementUm = nan;
    else
        confirmedImprovementUm = previousMeasuredRmsUm - measuredRmsUm;
    end

    iterRecord = emptyIterationRecord();
    iterRecord.iteration = iterationIndex;
    iterRecord.transformBefore = currentTransform;
    iterRecord.transformCorrection = correctionAchievedToRequested;
    iterRecord.transformAfter = candidateTransform;
    iterRecord.tiffPath = char(tiffPath);
    iterRecord.matchPairs = matchPairs;
    iterRecord.requestedRefXY = currentPredictedRefXY;
    iterRecord.achievedRefXY = achievedRefXY;
    iterRecord.matchedRequestedRefXY = matchedRequestedRefXY;
    iterRecord.matchedAchievedRefXY = matchedAchievedRefXY;
    iterRecord.preResidualUm = measuredResidualUm;
    iterRecord.postResidualUm = predictedResidualAfterUpdateUm;
    iterRecord.rmsBeforeUm = measuredRmsUm;
    iterRecord.rmsAfterUm = predictedRmsAfterUpdateUm;
    iterRecord.improvementUm = confirmedImprovementUm;
    iterRecord.accepted = false;
    iterRecord.iterationDir = iterationDir;
    result.iterations(end + 1, 1) = iterRecord;

    if measuredRmsUm < bestMeasuredRmsUm
        bestMeasuredRmsUm = measuredRmsUm;
        bestTransform = currentTransform;
        result.bestIteration = iterationIndex;
        result.bestTransform = bestTransform;
    end

    if ~isnan(confirmedImprovementUm) && confirmedImprovementUm < opts.MinImprovementUm
        if opts.StopOnNonImprovement
            stopRequested = true;
        end
    end
    if stopRequested
        writeIterationArtifacts(result.iterations(end), correctedAchievedRefXY, ...
            fullfile(iterationDir, 'iteration_summary'));
        break;
    end

    result.iterations(end).accepted = true;
    writeIterationArtifacts(result.iterations(end), correctedAchievedRefXY, ...
        fullfile(iterationDir, 'iteration_summary'));

    currentTransform = candidateTransform;
    previousMeasuredRmsUm = measuredRmsUm;

    if ~isempty(opts.MoveAfterIterationFcn) && iterationIndex < opts.MaxIterations
        moveState = iterState;
        moveState.currentTransform = currentTransform;
        moveState.currentRequestedRefXY = requestedRefXY;
        moveState.transformAfter = currentTransform;
        moveState.requestedRefXYAfter = moveState.currentRequestedRefXY;
        moveState.nextIteration = iterationIndex + 1;
        opts.MoveAfterIterationFcn(moveState);
        opts.ApplyTransformFcn(currentTransform, moveState);
    end
end

result.bestTransform = bestTransform;
result.requestedRefXY = requestedRefXY;
opts.ApplyTransformFcn(bestTransform, opts.State);
opts.SaveTransformFcn(bestTransform, fullfile(outputDir, 'best_transform.mat'), opts.State);
saveFinalTransform(result, fullfile(outputDir, 'final_transform.mat'));
writeFinalSummary(result, fullfile(outputDir, 'final_summary.txt'));
end


function namedArgs = namedArgsFromStruct(s)
fieldNames = fieldnames(s);
namedArgs = cell(1, numel(fieldNames) * 2);
for fieldIndex = 1:numel(fieldNames)
    namedArgs{2 * fieldIndex - 1} = fieldNames{fieldIndex};
    namedArgs{2 * fieldIndex} = s.(fieldNames{fieldIndex});
end
end


function roiGroup = defaultLoadStimGroup(roiPath, ~)
ensureBundledScanImageOnPath();
roiGroup = scanimage.mroi.RoiGroup.loadFromFile(char(roiPath));
end


function defaultApplyTransformError(~, ~)
error(['ApplyTransformFcn was not provided. Supply a callback that applies the ' ...
       'current scanner->reference transform to the active photostim scanner.']);
end


function defaultBurnAndAcquireError(~, ~)
error(['BurnAndAcquireFcn was not provided. Supply a callback that burns the pattern ' ...
       'and returns the acquired TIFF path for that iteration.']);
end


function defaultDetectHolesError(~, ~)
error(['DetectHolesFcn was not provided. Supply a callback that returns an Nx2 array ' ...
       'of detected hole centers in native reference-space coordinates.']);
end


function defaultSaveTransform(transformMatrix, savePath, ~)
T = transformMatrix;
save(savePath, 'T');
end


function saveFinalTransform(result, savePath)
T = result.bestTransform;
bestTransform = result.bestTransform;
bestIteration = result.bestIteration;
requestedRefXY = result.requestedRefXY;
scannerXY = result.scannerXY;
outputDir = result.outputDir;
save(savePath, 'T', 'bestTransform', 'bestIteration', 'requestedRefXY', 'scannerXY', 'outputDir');
end


function centersXY = extractStimulusCenters(roiGroup)
targets = enumerateStimulusTargets(roiGroup);
centersXY = zeros(numel(targets), 2);
for targetIndex = 1:numel(targets)
    centersXY(targetIndex, :) = targets(targetIndex).centerXY;
end
end


function xyOut = applyAffineToPoints(xyIn, transformMatrix)
xyIn = double(xyIn);
assert(size(xyIn, 2) == 2, 'Expected Nx2 point array.');
assert(isequal(size(transformMatrix), [3 3]), 'Expected a 3x3 affine matrix.');

nPoints = size(xyIn, 1);
homogeneous = [xyIn, ones(nPoints, 1)];
mapped = (transformMatrix * homogeneous.').';
w = mapped(:, 3);
assert(all(abs(w) > eps), 'Affine transform produced homogeneous w = 0.');
xyOut = mapped(:, 1:2) ./ w;
end


function [matchPairs, matchedTargetXY, matchedAchievedXY] = matchTargetAndAchievedPoints(targetXY, achievedXY, maxDistanceUm)
distanceMatrix = pairwiseDistanceMatrix(targetXY, achievedXY);

if exist('matchpairs', 'file') == 2
    if isinf(maxDistanceUm)
        finiteDistances = distanceMatrix(isfinite(distanceMatrix));
        if isempty(finiteDistances)
            costUnmatched = 1;
        else
            costUnmatched = max(finiteDistances(:)) + 1;
        end
    else
        costUnmatched = maxDistanceUm;
    end
    pairRows = matchpairs(distanceMatrix, costUnmatched);
    matchPairs = pairRows;
else
    matchPairs = greedyMatchPairs(distanceMatrix, maxDistanceUm);
end

matchedTargetXY = targetXY(matchPairs(:, 1), :);
matchedAchievedXY = achievedXY(matchPairs(:, 2), :);
end


function pairRows = greedyMatchPairs(distanceMatrix, maxDistanceUm)
[targetIndexGrid, achievedIndexGrid] = ndgrid(1:size(distanceMatrix, 1), 1:size(distanceMatrix, 2));
candidateRows = [targetIndexGrid(:), achievedIndexGrid(:), distanceMatrix(:)];
candidateRows = sortrows(candidateRows, 3, 'ascend');

usedTargets = false(size(distanceMatrix, 1), 1);
usedAchieved = false(size(distanceMatrix, 2), 1);
pairRows = zeros(0, 2);

for rowIndex = 1:size(candidateRows, 1)
    targetIndex = candidateRows(rowIndex, 1);
    achievedIndex = candidateRows(rowIndex, 2);
    distanceUm = candidateRows(rowIndex, 3);

    if distanceUm > maxDistanceUm
        break;
    end
    if usedTargets(targetIndex) || usedAchieved(achievedIndex)
        continue;
    end

    pairRows(end + 1, :) = [targetIndex, achievedIndex]; %#ok<AGROW>
    usedTargets(targetIndex) = true;
    usedAchieved(achievedIndex) = true;
end
end


function distanceMatrix = pairwiseDistanceMatrix(xyA, xyB)
dx = xyA(:, 1) - xyB(:, 1).';
dy = xyA(:, 2) - xyB(:, 2).';
distanceMatrix = hypot(dx, dy);
end


function transformMatrix = fitAffineCorrection(sourceXY, targetXY, useFullAffine, useScaleCorrection)
assert(size(sourceXY, 1) == size(targetXY, 1), 'Source and target point counts must match.');
assert(size(sourceXY, 2) == 2 && size(targetXY, 2) == 2, 'Expected Nx2 point arrays.');

if useFullAffine
    design = [sourceXY, ones(size(sourceXY, 1), 1)];
    coeffX = design \ targetXY(:, 1);
    coeffY = design \ targetXY(:, 2);
    transformMatrix = [coeffX.'; coeffY.'; 0 0 1];
elseif useScaleCorrection
    coeffX = [sourceXY(:, 1), ones(size(sourceXY, 1), 1)] \ targetXY(:, 1);
    coeffY = [sourceXY(:, 2), ones(size(sourceXY, 1), 1)] \ targetXY(:, 2);
    transformMatrix = [coeffX(1) 0 coeffX(2); 0 coeffY(1) coeffY(2); 0 0 1];
else
    delta = mean(targetXY - sourceXY, 1);
    transformMatrix = [1 0 delta(1); 0 1 delta(2); 0 0 1];
end
end


function residualUm = rowwiseDistance(xyA, xyB)
delta = double(xyA) - double(xyB);
residualUm = hypot(delta(:, 1), delta(:, 2));
end


function value = rmsMagnitude(values)
value = sqrt(mean(values .^ 2));
end


function record = emptyIterationRecord()
record = struct( ...
    'iteration', [], ...
    'transformBefore', [], ...
    'transformCorrection', [], ...
    'transformAfter', [], ...
    'tiffPath', '', ...
    'matchPairs', zeros(0, 2), ...
    'requestedRefXY', zeros(0, 2), ...
    'achievedRefXY', zeros(0, 2), ...
    'matchedRequestedRefXY', zeros(0, 2), ...
    'matchedAchievedRefXY', zeros(0, 2), ...
    'preResidualUm', zeros(0, 1), ...
    'postResidualUm', zeros(0, 1), ...
    'rmsBeforeUm', [], ...
    'rmsAfterUm', [], ...
    'improvementUm', [], ...
    'accepted', false, ...
    'iterationDir', '');
end


function writeIterationArtifacts(iterRecord, correctedAchievedRefXY, outputStem)
matchedTable = table( ...
    iterRecord.matchPairs(:, 1), ...
    iterRecord.matchPairs(:, 2), ...
    iterRecord.matchedRequestedRefXY(:, 1), ...
    iterRecord.matchedRequestedRefXY(:, 2), ...
    iterRecord.matchedAchievedRefXY(:, 1), ...
    iterRecord.matchedAchievedRefXY(:, 2), ...
    iterRecord.preResidualUm, ...
    correctedAchievedRefXY(:, 1), ...
    correctedAchievedRefXY(:, 2), ...
    iterRecord.postResidualUm, ...
    'VariableNames', { ...
        'target_index', 'achieved_index', ...
        'predicted_x_ref', 'predicted_y_ref', ...
        'achieved_x_ref', 'achieved_y_ref', ...
        'residual_measured_ref', ...
        'fit_corrected_achieved_x_ref', 'fit_corrected_achieved_y_ref', ...
        'fit_residual_after_candidate_ref'});
    writetable(matchedTable, [outputStem '_matched.csv']);

    fid = fopen([outputStem '.txt'], 'w');
    cleanupObj = onCleanup(@() fclose(fid));
    fprintf(fid, 'Iteration: %d\n', iterRecord.iteration);
    fprintf(fid, 'Matched points: %d\n', size(iterRecord.matchPairs, 1));
    fprintf(fid, 'Measured RMS at current transform (ref units): %.6f\n', iterRecord.rmsBeforeUm);
    fprintf(fid, 'Fit residual after candidate update (ref units): %.6f\n', iterRecord.rmsAfterUm);
    fprintf(fid, 'Confirmed improvement from previous burn (ref units): %.6f\n', iterRecord.improvementUm);
    fprintf(fid, 'Accepted: %d\n', iterRecord.accepted);
    fprintf(fid, '\nCorrection achieved->requested:\n');
    fprintfAffineMatrix(fid, iterRecord.transformCorrection);
    fprintf(fid, '\nTransform after update:\n');
    fprintfAffineMatrix(fid, iterRecord.transformAfter);
end


function writeFinalSummary(result, outputPath)
fid = fopen(outputPath, 'w');
    cleanupObj = onCleanup(@() fclose(fid));
fprintf(fid, 'Pattern ROI: %s\n', result.patternRoiPath);
fprintf(fid, 'Iterations run: %d\n', numel(result.iterations));
fprintf(fid, 'Best iteration: %d\n', result.bestIteration);
fprintf(fid, '\nBest transform:\n');
fprintfAffineMatrix(fid, result.bestTransform);

for iterationIndex = 1:numel(result.iterations)
    iterRecord = result.iterations(iterationIndex);
    fprintf(fid, '\nIteration %d\n', iterRecord.iteration);
    fprintf(fid, '  matches: %d\n', size(iterRecord.matchPairs, 1));
    fprintf(fid, '  measured rms at current transform (ref units): %.6f\n', iterRecord.rmsBeforeUm);
    fprintf(fid, '  fit residual after candidate update (ref units): %.6f\n', iterRecord.rmsAfterUm);
    fprintf(fid, '  confirmed improvement from previous burn (ref units): %.6f\n', iterRecord.improvementUm);
    fprintf(fid, '  accepted: %d\n', iterRecord.accepted);
end
end


function fprintfAffineMatrix(fid, transformMatrix)
for rowIndex = 1:size(transformMatrix, 1)
    fprintf(fid, '  %.12g, %.12g, %.12g\n', transformMatrix(rowIndex, 1), transformMatrix(rowIndex, 2), transformMatrix(rowIndex, 3));
end
end
