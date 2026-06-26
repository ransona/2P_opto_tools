function targets = enumerateStimulusTargets(roiGroupOrPath)
%ENUMERATESTIMULUSTARGETS Return one entry per burnable stimulus target.
%   ScanImage SLM photostim groups can store many target points in one ROI's
%   scanfield.slmPattern matrix. This helper exposes those rows as individual
%   holes. If no SLM pattern rows are present, it falls back to one target per
%   top-level ROI for compatibility with older single-point ROI files.

if ischar(roiGroupOrPath) || isstring(roiGroupOrPath)
    ensureBundledScanImageOnPath();
    roiGroup = scanimage.mroi.RoiGroup.loadFromFile(char(roiGroupOrPath));
else
    roiGroup = roiGroupOrPath;
end

assert(isprop(roiGroup, 'rois') && ~isempty(roiGroup.rois), 'ROI group has no ROIs.');

targets = emptyTargetStruct();
fallbackTargets = emptyTargetStruct();

for roiIndex = 1:numel(roiGroup.rois)
    roi = roiGroup.rois(roiIndex);
    assert(~isempty(roi.scanfields), 'ROI %d has no scanfields.', roiIndex);
    scanfield = roi.scanfields(1);
    assert(isprop(scanfield, 'centerXY'), 'ROI %d scanfield lacks centerXY.', roiIndex);

    roiName = localFallbackName(roi, roiIndex);
    centerXY = double(scanfield.centerXY(:)');
    fallbackTargets(end + 1, 1) = makeTarget(roiIndex, nan, centerXY, roiName, []); %#ok<AGROW>

    slmPattern = getSlmPattern(scanfield);
    if isempty(slmPattern)
        continue;
    end

    for patternIndex = 1:size(slmPattern, 1)
        patternRow = double(slmPattern(patternIndex, :));
        targetName = sprintf('%s point %02d', roiName, patternIndex);
        targets(end + 1, 1) = makeTarget(roiIndex, patternIndex, patternRow(1:2), targetName, patternRow); %#ok<AGROW>
    end
end

if isempty(targets)
    targets = fallbackTargets;
end

for targetIndex = 1:numel(targets)
    targets(targetIndex).targetIndex = targetIndex;
end
end


function targets = emptyTargetStruct()
targets = struct( ...
    'targetIndex', {}, ...
    'roiIndex', {}, ...
    'slmPatternIndex', {}, ...
    'centerXY', {}, ...
    'name', {}, ...
    'slmPatternRow', {});
end


function target = makeTarget(roiIndex, slmPatternIndex, centerXY, name, slmPatternRow)
target = struct( ...
    'targetIndex', [], ...
    'roiIndex', roiIndex, ...
    'slmPatternIndex', slmPatternIndex, ...
    'centerXY', centerXY, ...
    'name', name, ...
    'slmPatternRow', slmPatternRow);
end


function slmPattern = getSlmPattern(scanfield)
slmPattern = [];
if isprop(scanfield, 'slmPattern')
    slmPattern = scanfield.slmPattern;
end
if isempty(slmPattern) || ~isnumeric(slmPattern) || size(slmPattern, 2) < 2
    slmPattern = [];
end
end


function name = localFallbackName(roi, roiIndex)
name = '';
if isprop(roi, 'name') && ~isempty(roi.name)
    name = char(roi.name);
end
if isempty(name)
    name = sprintf('ROI %d', roiIndex);
end
end
