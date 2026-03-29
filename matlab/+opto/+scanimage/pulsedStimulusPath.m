function [xx, yy] = pulsedStimulusPath(tt, varargin)
% pulsedStimulusPath
% Encode one full stim cycle as:
% - hold at center during pre-pause
% - run the delegate stimulus function during the active interval
% - hold at center for the remainder of the cycle

inputs = scanimage.mroi.util.parseInputs(varargin);

if ~isfield(inputs, 'prePause_s') || isempty(inputs.prePause_s)
    inputs.prePause_s = 0;
end
if ~isfield(inputs, 'stimActive_s') || isempty(inputs.stimActive_s)
    inputs.stimActive_s = 0;
end
if ~isfield(inputs, 'delegateFunction') || isempty(inputs.delegateFunction)
    inputs.delegateFunction = 'scanimage.mroi.stimulusfunctions.logspiral';
end
if ~isfield(inputs, 'delegateParams') || isempty(inputs.delegateParams)
    inputs.delegateParams = {};
end

prePause_s = double(inputs.prePause_s);
stimActive_s = double(inputs.stimActive_s);
delegateParams = inputs.delegateParams;
delegateFunction = inputs.delegateFunction;
if isa(delegateFunction, 'function_handle')
    delegateFcn = delegateFunction;
else
    delegateFcn = str2func(char(string(delegateFunction)));
end

xx = zeros(size(tt));
yy = zeros(size(tt));

activeStart_s = max(0, prePause_s);
activeStop_s = max(activeStart_s, activeStart_s + stimActive_s);
activeMask = (tt >= activeStart_s) & (tt < activeStop_s);
if ~any(activeMask)
    return;
end

activeT = tt(activeMask) - activeStart_s;
if activeStop_s > activeStart_s
    if numel(activeT) == 1
        delegateT = [0 stimActive_s];
        [delegateX, delegateY] = delegateFcn(delegateT, delegateParams{:});
        xx(activeMask) = delegateX(1);
        yy(activeMask) = delegateY(1);
    else
        [delegateX, delegateY] = delegateFcn(activeT, delegateParams{:});
        xx(activeMask) = delegateX(:).';
        yy(activeMask) = delegateY(:).';
    end
end
end
