function powers = pulsedBeamPowers(tt, powerFracs, varargin)
% pulsedBeamPowers
% Gate beam output so one scanfield cycle contains:
% - zero power during pre-pause
% - constant requested power during active interval
% - zero power during the off tail

inputs = scanimage.mroi.util.parseInputs(varargin);
if ~isfield(inputs, 'prePause_s') || isempty(inputs.prePause_s)
    inputs.prePause_s = 0;
end
if ~isfield(inputs, 'stimActive_s') || isempty(inputs.stimActive_s)
    inputs.stimActive_s = max(tt) - min(tt);
end
if ~isfield(inputs, 'cycleDuration_s') || isempty(inputs.cycleDuration_s)
    inputs.cycleDuration_s = [];
end
if ~isfield(inputs, 'patternOffset_s') || isempty(inputs.patternOffset_s)
    inputs.patternOffset_s = 0;
end
if ~isfield(inputs, 'totalPatternDuration_s') || isempty(inputs.totalPatternDuration_s)
    inputs.totalPatternDuration_s = inf;
end

prePause_s = double(inputs.prePause_s);
stimActive_s = double(inputs.stimActive_s);
cycleDuration_s = double(inputs.cycleDuration_s);
patternOffset_s = double(inputs.patternOffset_s);
totalPatternDuration_s = double(inputs.totalPatternDuration_s);
activeStart_s = max(0, prePause_s);
activeStop_s = activeStart_s + max(0, stimActive_s);

powers = zeros(numel(tt), numel(powerFracs));
absT = tt + patternOffset_s;
validMask = absT >= 0 & absT < totalPatternDuration_s;
if isempty(cycleDuration_s) || cycleDuration_s <= 0
    cycleT = absT;
else
    cycleT = mod(absT, cycleDuration_s);
end
activeMask = validMask & (cycleT >= activeStart_s) & (cycleT < activeStop_s);
if any(activeMask)
    powers(activeMask, :) = repmat(powerFracs, nnz(activeMask), 1);
end
end
