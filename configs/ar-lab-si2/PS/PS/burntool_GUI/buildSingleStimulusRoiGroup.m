function singleRoiGroup = buildSingleStimulusRoiGroup(roiGroupOrPath, targetIndex)
%BUILDSINGLESTIMULUSROIGROUP Create a stimulus group containing one target.
%   SINGLEROIGROUP = BUILDSINGLESTIMULUSROIGROUP(ROIGROUPORPATH, TARGETINDEX)
%   returns a copy of the source stimulus group with only TARGETINDEX retained.

if ischar(roiGroupOrPath) || isstring(roiGroupOrPath)
    ensureBundledScanImageOnPath();
    sourceGroup = scanimage.mroi.RoiGroup.loadFromFile(char(roiGroupOrPath));
else
    sourceGroup = roiGroupOrPath;
end

targets = enumerateStimulusTargets(sourceGroup);
assert(targetIndex >= 1 && targetIndex <= numel(targets), ...
    'Target index %d is out of range for a group with %d target(s).', ...
    targetIndex, numel(targets));
target = targets(targetIndex);

groupName = sprintf('Target %d', targetIndex);
if isprop(sourceGroup, 'name') && ~isempty(sourceGroup.name)
    groupName = sprintf('%s - Target %d', char(sourceGroup.name), targetIndex);
end

singleRoiGroup = scanimage.mroi.RoiGroup(groupName);
if isprop(sourceGroup, 'UserData')
    singleRoiGroup.UserData = sourceGroup.UserData;
end

singleRoi = sourceGroup.rois(target.roiIndex).copy();
if ~isnan(target.slmPatternIndex)
    scanfield = singleRoi.scanfields(1);
    scanfield.slmPattern = target.slmPatternRow;
    singleRoi.scanfields(1) = scanfield;
end
singleRoiGroup.add(singleRoi);
end
