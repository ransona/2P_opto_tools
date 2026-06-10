function [refXY, diagnostics] = detectBurnHolesFromTiff(tiffPath, varargin)
%DETECTBURNHOLESFROMTIFF Detect dark burn holes and return reference-space XY.

parser = inputParser();
parser.addRequired('tiffPath', @(x) ischar(x) || isstring(x));
parser.addParameter('PatternRoiPath', '', @(x) ischar(x) || isstring(x));
parser.addParameter('MetadataTiffPath', '', @(x) ischar(x) || isstring(x));
parser.addParameter('BaselineTiffPath', '', @(x) ischar(x) || isstring(x));
parser.addParameter('ThresholdSigma', 3, @(x) isnumeric(x) && isscalar(x) && x > 0);
parser.addParameter('MinSeparationRef', [], @(x) isempty(x) || (isnumeric(x) && isscalar(x) && x > 0));
parser.addParameter('WriteArtifacts', true, @(x) islogical(x) || isnumeric(x));
parser.parse(tiffPath, varargin{:});

tiffPath = char(tiffPath);
assert(exist(tiffPath, 'file') == 2, 'TIFF not found: %s', tiffPath);

img = readMeanImage(tiffPath);
baselinePath = resolveBaselineTiffPath(tiffPath, char(parser.Results.BaselineTiffPath));
baselineImg = [];
if ~isempty(baselinePath)
    baselineImg = readMeanImage(baselinePath);
    assert(isequal(size(baselineImg), size(img)), ...
        'Baseline and post-burn TIFF sizes do not match.');
end
metadataPath = resolveMetadataTiffPath(tiffPath, char(parser.Results.MetadataTiffPath));
[pixelToRefTransform, pixelResolutionXY] = readScanImagePixelToRefTransform(metadataPath);
pixelSizeRef = mean(vecnorm(pixelToRefTransform(1:2, 1:2), 2, 1));
spiralWidthRef = inferSpiralWidthRef(char(parser.Results.PatternRoiPath));
patternMinSeparationRef = inferPatternMinSeparationRef(char(parser.Results.PatternRoiPath));
minSeparationRef = resolveMinSeparationRef(parser.Results.MinSeparationRef, patternMinSeparationRef, spiralWidthRef);
expectedDiameterPx = max(3, spiralWidthRef / pixelSizeRef);
expectedHoleCount = inferExpectedHoleCount(char(parser.Results.PatternRoiPath));

scale = detectionScale(expectedDiameterPx, minSeparationRef / pixelSizeRef);
[centroidsXY, mask, residual] = detectDarkPuncta(img, baselineImg, scale, parser.Results.ThresholdSigma, expectedHoleCount);
refXY = pixelsToReferenceXY(centroidsXY, pixelToRefTransform);

diagnostics = struct( ...
    'tiffPath', tiffPath, ...
    'baselineTiffPath', baselinePath, ...
    'metadataTiffPath', metadataPath, ...
    'patternRoiPath', char(parser.Results.PatternRoiPath), ...
    'pixelResolutionXY', pixelResolutionXY, ...
    'pixelToRefTransform', pixelToRefTransform, ...
    'pixelSizeRef', pixelSizeRef, ...
    'spiralWidthRef', spiralWidthRef, ...
    'patternMinSeparationRef', patternMinSeparationRef, ...
    'minSeparationRef', minSeparationRef, ...
    'expectedHoleCount', expectedHoleCount, ...
    'expectedDiameterPx', expectedDiameterPx, ...
    'scale', scale, ...
    'centroidsXY', centroidsXY, ...
    'refXY', refXY, ...
    'numHoles', size(refXY, 1));

if logical(parser.Results.WriteArtifacts)
    writeDetectionArtifacts(tiffPath, img, mask, residual, diagnostics);
end
end


function baselinePath = resolveBaselineTiffPath(tiffPath, requestedPath)
baselinePath = char(requestedPath);
if ~isempty(baselinePath)
    assert(exist(baselinePath, 'file') == 2, 'Baseline TIFF not found: %s', baselinePath);
    return;
end

[folderPath, ~] = fileparts(tiffPath);
candidatePath = fullfile(folderPath, 'pre_burn_mean.tif');
if exist(candidatePath, 'file') == 2
    baselinePath = candidatePath;
else
    baselinePath = '';
end
end


function img = readMeanImage(tiffPath)
info = imfinfo(tiffPath);
acc = [];
nAccum = 0;
for frameIndex = 1:numel(info)
    frame = imread(tiffPath, frameIndex, 'Info', info);
    if isempty(acc)
        acc = zeros(size(frame), 'double');
    elseif ~isequal(size(frame), size(acc))
        continue;
    end
    acc = acc + double(frame);
    nAccum = nAccum + 1;
end
assert(nAccum > 0, 'Could not read image data from %s.', tiffPath);
img = acc ./ nAccum;
end


function metadataPath = resolveMetadataTiffPath(tiffPath, requestedPath)
if ~isempty(requestedPath)
    metadataPath = requestedPath;
    assert(exist(metadataPath, 'file') == 2, 'Metadata TIFF not found: %s', metadataPath);
    return;
end
if hasScanImageArtistMetadata(tiffPath)
    metadataPath = tiffPath;
    return;
end

[folderPath, fileStem] = fileparts(tiffPath);
logStem = regexprep(fileStem, '_mean$', '');
files = dir(fullfile(folderPath, [logStem '_*.tif']));
files = files(~endsWith({files.name}, '_mean.tif'));
assert(~isempty(files), 'No sibling raw ScanImage TIFF found for %s.', tiffPath);
[~, order] = sort([files.datenum], 'descend');
for fileIndex = order
    candidatePath = fullfile(files(fileIndex).folder, files(fileIndex).name);
    if hasScanImageArtistMetadata(candidatePath)
        metadataPath = candidatePath;
        return;
    end
end
error('No sibling raw ScanImage TIFF with Artist metadata found for %s.', tiffPath);
end


function tf = hasScanImageArtistMetadata(tiffPath)
tf = false;
try
    info = imfinfo(tiffPath);
    tf = isfield(info(1), 'Artist') && contains(char(info(1).Artist), '"RoiGroups"');
catch
end
end


function [pixelToRefTransform, pixelResolutionXY] = readScanImagePixelToRefTransform(metadataPath)
info = imfinfo(metadataPath);
metadata = jsondecode(char(info(1).Artist));
scanfields = metadata.RoiGroups.imagingRoiGroup.rois.scanfields;
scanfield = scanfields(1);
pixelResolutionXY = double(scanfield.pixelResolutionXY(:).');
pixelToRefTransform = double(scanfield.pixelToRefTransform);
assert(isequal(size(pixelToRefTransform), [3 3]), ...
    'Expected a 3x3 pixelToRefTransform in %s.', metadataPath);
end


function spiralWidthRef = inferSpiralWidthRef(patternRoiPath)
if isempty(patternRoiPath) || exist(patternRoiPath, 'file') ~= 2
    spiralWidthRef = 0.15;
    return;
end

ensureBundledScanImageOnPath();
roiGroup = scanimage.mroi.RoiGroup.loadFromFile(patternRoiPath);
for roiIndex = 1:numel(roiGroup.rois)
    scanfield = roiGroup.rois(roiIndex).scanfields(1);
    if isprop(scanfield, 'slmPattern') && ~isempty(scanfield.slmPattern) ...
            && isprop(scanfield, 'sizeXY') && ~isempty(scanfield.sizeXY)
        spiralWidthRef = mean(double(scanfield.sizeXY(:)));
        return;
    end
end

spiralWidthRef = 0.15;
end


function minSeparationRef = inferPatternMinSeparationRef(patternRoiPath)
minSeparationRef = nan;
if isempty(patternRoiPath) || exist(patternRoiPath, 'file') ~= 2
    return;
end
try
    targets = enumerateStimulusTargets(patternRoiPath);
    targetXY = vertcat(targets.centerXY);
    if size(targetXY, 1) < 2
        return;
    end
    distances = pairwiseDistanceMatrix(targetXY, targetXY);
    distances(distances == 0) = inf;
    minSeparationRef = min(distances(:));
catch
    minSeparationRef = nan;
end
end


function minSeparationRef = resolveMinSeparationRef(requestedMinSeparationRef, patternMinSeparationRef, spiralWidthRef)
if ~isempty(requestedMinSeparationRef)
    minSeparationRef = double(requestedMinSeparationRef);
elseif isfinite(patternMinSeparationRef) && patternMinSeparationRef > 0
    minSeparationRef = 0.75 * patternMinSeparationRef;
else
    minSeparationRef = 5 * spiralWidthRef;
end
end


function expectedHoleCount = inferExpectedHoleCount(patternRoiPath)
expectedHoleCount = inf;
if isempty(patternRoiPath) || exist(patternRoiPath, 'file') ~= 2
    return;
end
try
    targets = enumerateStimulusTargets(patternRoiPath);
    expectedHoleCount = numel(targets);
catch
    expectedHoleCount = inf;
end
end


function scale = detectionScale(expectedDiameterPx, minSeparationPx)
expectedAreaPx = pi * (expectedDiameterPx / 2) ^ 2;
scale = struct( ...
    'smoothSigmaPx', max(0.5, expectedDiameterPx / 5), ...
    'backgroundSigmaPx', max(8, expectedDiameterPx * 3), ...
    'closeRadiusPx', max(1, round(expectedDiameterPx / 5)), ...
    'borderMarginPx', max(6, round(expectedDiameterPx * 2)), ...
    'centroidRadiusPx', max(2, round(expectedDiameterPx / 2)), ...
    'minSeparationPx', max(3, minSeparationPx), ...
    'minAreaPx', max(4, round(expectedAreaPx * 0.2)), ...
    'maxAreaPx', max(20, round(expectedAreaPx * 4)));
end


function [centroidsXY, mask, residual] = detectDarkPuncta(img, baselineImg, scale, thresholdSigma, expectedHoleCount)
if isempty(baselineImg)
    smoothed = imgaussfilt(img, scale.smoothSigmaPx);
    background = imgaussfilt(smoothed, scale.backgroundSigmaPx);
    residual = background - smoothed;
else
    deltaDarkening = baselineImg - img;
    smoothed = imgaussfilt(deltaDarkening, scale.smoothSigmaPx);
    background = imgaussfilt(smoothed, scale.backgroundSigmaPx);
    residual = smoothed - background;
end
residual = residual - median(residual(:));
robustSigma = 1.4826 * mad(residual(:), 1);
if robustSigma <= 0 || ~isfinite(robustSigma)
    robustSigma = std(residual(:), 0, 'omitnan');
end
if robustSigma <= 0 || ~isfinite(robustSigma)
    centroidsXY = zeros(0, 2);
    mask = false(size(residual));
    return;
end

mask = residual > thresholdSigma * robustSigma;
mask = clearBorderMargin(mask, scale.borderMarginPx);
peakMask = imregionalmax(residual) & mask;
[peakY, peakX] = find(peakMask);
scores = residual(peakMask);
if isempty(scores)
    centroidsXY = zeros(0, 2);
    mask = false(size(residual));
    return;
end

[scores, order] = sort(scores, 'descend');
peakX = peakX(order);
peakY = peakY(order);
selected = selectSeparatedPeaks(peakX, peakY, scores, scale.minSeparationPx, expectedHoleCount);
if isempty(selected)
    centroidsXY = zeros(0, 2);
    mask = false(size(residual));
    return;
end
centroidsXY = refinePeakCentroids(residual, peakX(selected), peakY(selected), scale.centroidRadiusPx);
if ~isempty(baselineImg)
    centroidsXY = keepBaselineDarkenedCandidates(centroidsXY, baselineImg - img, scale);
end
centroidsXY = sortrows(centroidsXY, [2 1]);
mask = selectedPeakMask(size(residual), centroidsXY, scale.centroidRadiusPx);
end


function centroidsXY = keepBaselineDarkenedCandidates(centroidsXY, deltaDarkening, scale)
if isempty(centroidsXY)
    return;
end

keep = false(size(centroidsXY, 1), 1);
[gridX, gridY] = meshgrid(1:size(deltaDarkening, 2), 1:size(deltaDarkening, 1));
centerRadiusPx = max(1, round(scale.centroidRadiusPx));
annulusInnerPx = max(centerRadiusPx + 2, round(scale.centroidRadiusPx * 2));
annulusOuterPx = max(annulusInnerPx + 2, round(scale.centroidRadiusPx * 4));

for idx = 1:size(centroidsXY, 1)
    radiusPx = hypot(gridX - centroidsXY(idx, 1), gridY - centroidsXY(idx, 2));
    centerValues = deltaDarkening(radiusPx <= centerRadiusPx);
    annulusValues = deltaDarkening(radiusPx >= annulusInnerPx & radiusPx <= annulusOuterPx);
    if isempty(centerValues) || isempty(annulusValues)
        continue;
    end

    centerDarkening = mean(centerValues, 'omitnan');
    localDarkening = centerDarkening - median(annulusValues, 'omitnan');
    keep(idx) = centerDarkening > 0 && localDarkening > 0;
end

centroidsXY = centroidsXY(keep, :);
end


function selected = selectSeparatedPeaks(peakX, peakY, scores, minSeparationPx, expectedHoleCount) %#ok<INUSD>
selected = zeros(0, 1);
for peakIndex = 1:numel(peakX)
    if isempty(selected) || all(hypot(peakX(peakIndex) - peakX(selected), peakY(peakIndex) - peakY(selected)) >= minSeparationPx)
        selected(end + 1, 1) = peakIndex; %#ok<AGROW>
    end
    if isfinite(expectedHoleCount) && numel(selected) >= expectedHoleCount
        break;
    end
end
end


function centroidsXY = refinePeakCentroids(residual, peakX, peakY, radiusPx)
centroidsXY = zeros(numel(peakX), 2);
[height, width] = size(residual);
for peakIndex = 1:numel(peakX)
    xRange = max(1, peakX(peakIndex) - radiusPx):min(width, peakX(peakIndex) + radiusPx);
    yRange = max(1, peakY(peakIndex) - radiusPx):min(height, peakY(peakIndex) + radiusPx);
    patch = residual(yRange, xRange);
    weights = patch - min(patch(:));
    weights(weights < 0) = 0;
    if sum(weights(:)) <= 0
        centroidsXY(peakIndex, :) = [peakX(peakIndex), peakY(peakIndex)];
        continue;
    end
    [gridX, gridY] = meshgrid(xRange, yRange);
    centroidsXY(peakIndex, :) = [ ...
        sum(gridX(:) .* weights(:)) / sum(weights(:)), ...
        sum(gridY(:) .* weights(:)) / sum(weights(:))];
end
end


function mask = selectedPeakMask(imageSize, centroidsXY, radiusPx)
mask = false(imageSize);
[gridX, gridY] = meshgrid(1:imageSize(2), 1:imageSize(1));
for idx = 1:size(centroidsXY, 1)
    mask = mask | hypot(gridX - centroidsXY(idx, 1), gridY - centroidsXY(idx, 2)) <= radiusPx;
end
end


function mask = clearBorderMargin(mask, marginPx)
marginPx = min(round(marginPx), floor(min(size(mask)) / 4));
if marginPx < 1
    return;
end
mask(1:marginPx, :) = false;
mask(end - marginPx + 1:end, :) = false;
mask(:, 1:marginPx) = false;
mask(:, end - marginPx + 1:end) = false;
end


function distanceMatrix = pairwiseDistanceMatrix(xyA, xyB)
distanceMatrix = hypot(xyA(:, 1) - xyB(:, 1).', xyA(:, 2) - xyB(:, 2).');
end


function refXY = pixelsToReferenceXY(centroidsXY, pixelToRefTransform)
homogeneousPixels = [centroidsXY, ones(size(centroidsXY, 1), 1)];
refHomogeneous = (pixelToRefTransform * homogeneousPixels.').';
refXY = refHomogeneous(:, 1:2) ./ refHomogeneous(:, 3);
end


function writeDetectionArtifacts(tiffPath, img, mask, residual, diagnostics)
[folderPath, fileStem] = fileparts(tiffPath);
writetable(table( ...
    (1:size(diagnostics.refXY, 1)).', ...
    diagnostics.centroidsXY(:, 1), diagnostics.centroidsXY(:, 2), ...
    diagnostics.refXY(:, 1), diagnostics.refXY(:, 2), ...
    'VariableNames', {'holeIndex', 'pixelX', 'pixelY', 'refX', 'refY'}), ...
    fullfile(folderPath, [fileStem '_detected_holes.csv']));

lo = prctile(img(:), 1);
hi = prctile(img(:), 99.8);
preview = uint8(255 * max(0, min(1, (img - lo) ./ max(eps, hi - lo))));
rgb = repmat(preview, 1, 1, 3);
outline = bwperim(mask);
rgb(:, :, 1) = max(rgb(:, :, 1), uint8(outline) * 255);
rgb(:, :, 2) = rgb(:, :, 2) .* uint8(~outline);
rgb(:, :, 3) = rgb(:, :, 3) .* uint8(~outline);
for idx = 1:size(diagnostics.centroidsXY, 1)
    rgb = drawCross(rgb, diagnostics.centroidsXY(idx, :), 5);
end
imwrite(rgb, fullfile(folderPath, [fileStem '_detected_holes.png']));

residualOut = uint16(round(rescale(residual) * double(intmax('uint16'))));
imwrite(residualOut, fullfile(folderPath, [fileStem '_detection_residual.tif']));
save(fullfile(folderPath, [fileStem '_detection_diagnostics.mat']), '-struct', 'diagnostics');
end


function rgb = drawCross(rgb, xy, radius)
x = round(xy(1));
y = round(xy(2));
[height, width, ~] = size(rgb);
xRange = max(1, x - radius):min(width, x + radius);
yRange = max(1, y - radius):min(height, y + radius);
rgb(y, xRange, 1) = 255;
rgb(y, xRange, 2) = 0;
rgb(y, xRange, 3) = 0;
rgb(yRange, x, 1) = 255;
rgb(yRange, x, 2) = 0;
rgb(yRange, x, 3) = 0;
end
