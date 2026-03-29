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

prePause_s = double(inputs.prePause_s);
stimActive_s = double(inputs.stimActive_s);
activeStart_s = max(0, prePause_s);
activeStop_s = activeStart_s + max(0, stimActive_s);

powers = zeros(numel(tt), numel(powerFracs));
activeMask = (tt >= activeStart_s) & (tt < activeStop_s);
if any(activeMask)
    powers(activeMask, :) = repmat(powerFracs, nnz(activeMask), 1);
end
end
