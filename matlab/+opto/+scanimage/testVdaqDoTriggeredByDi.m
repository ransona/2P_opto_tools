function do_task = testVdaqDoTriggeredByDi(varargin)
% testVdaqDoTriggeredByDi
% Create and arm a raw vDAQ digital-output task using the ScanImage vDAQ API
% pattern shown in:
% https://docs.scanimage.org/Advanced%2BFeatures/ScanImage%2BAPI/vDAQ%2BAPI.html
%
% Default behavior:
% - output on D1.6
% - start trigger on D2.1
% - 5 pulses at 10 Hz
% - 10 ms pulse width
% - 2 MHz sample rate
%
% Usage:
%   do_task = opto.scanimage.testVdaqDoTriggeredByDi();
%
% After calling:
% - if startTrigger is non-empty, the task is armed and waiting for that edge
% - if startTrigger is empty, the task starts immediately
% The task handle is also stored in the base workspace as
%   optoPhotostimDebugDoTask

    p = inputParser();
    addParameter(p, 'outputLine', 'D1.6');
    addParameter(p, 'startTrigger', 'D2.1');
    addParameter(p, 'sampleRate_Hz', 2e6);
    addParameter(p, 'pulseTimes_s', [0.1 0.2 0.3 0.4 0.5]);
    addParameter(p, 'pulseWidth_s', 10e-3);
    addParameter(p, 'taskName', 'Opto Photostim DO Debug');
    parse(p, varargin{:});

    outputLine = char(string(p.Results.outputLine));
    startTrigger = char(string(p.Results.startTrigger));
    sampleRate_Hz = double(p.Results.sampleRate_Hz);
    pulseTimes_s = double(p.Results.pulseTimes_s(:));
    pulseWidth_s = double(p.Results.pulseWidth_s);
    taskName = char(string(p.Results.taskName));

    assert(isfinite(sampleRate_Hz) && sampleRate_Hz > 0, 'sampleRate_Hz must be positive.');
    assert(all(isfinite(pulseTimes_s)) && all(pulseTimes_s >= 0), 'pulseTimes_s must be non-negative.');
    assert(isfinite(pulseWidth_s) && pulseWidth_s > 0, 'pulseWidth_s must be positive.');

    resourceStore = dabs.resources.ResourceStore();
    vDAQ = resourceStore.filterByName('vDAQ0');
    if iscell(vDAQ)
        vDAQ = vDAQ{1};
    end
    assert(most.idioms.isValidObj(vDAQ), 'vDAQ0 resource was not found.');

    totalDuration_s = max(pulseTimes_s) + pulseWidth_s + 0.1;
    totalSamples = max(1, ceil(totalDuration_s * sampleRate_Hz));
    pulseWidth_samples = max(1, round(pulseWidth_s * sampleRate_Hz));

    outputWaveform = zeros(totalSamples, 1);
    for idx = 1:numel(pulseTimes_s)
        startIdx = min(totalSamples, max(1, round(pulseTimes_s(idx) * sampleRate_Hz) + 1));
        endIdx = min(totalSamples, startIdx + pulseWidth_samples - 1);
        outputWaveform(startIdx:endIdx) = 1;
    end

    taskVarName = 'optoPhotostimDebugDoTask';
    if evalin('base', sprintf("exist('%s','var')", taskVarName))
        oldTask = evalin('base', taskVarName);
        if most.idioms.isValidObj(oldTask)
            try
                oldTask.abort();
            catch
            end
            try
                delete(oldTask);
            catch
            end
        end
    end

    % Follow the documented vDAQ API task pattern directly.
    do_task = dabs.vidrio.ddi.DoTask(vDAQ.hDevice, taskName);
    do_task.addChannel(outputLine, 'Photostim Trigger Out');
    do_task.writeOutputBuffer(outputWaveform);
    do_task.sampleRate = sampleRate_Hz;
    do_task.sampleMode = 'finite';
    do_task.samplesPerTrigger = numel(outputWaveform);
    do_task.allowRetrigger = false;
    if isempty(startTrigger)
        do_task.startTrigger = '';
    else
        do_task.startTrigger = startTrigger;
        do_task.startTriggerEdge = 'rising';
    end

    assignin('base', taskVarName, do_task);

    disp('----------');
    disp('testVdaqDoTriggeredByDi');
    fprintf('Output line: %s\n', outputLine);
    fprintf('Start trigger: %s\n', most.idioms.ifthenelse(isempty(startTrigger), '<software start>', startTrigger));
    fprintf('Sample rate (Hz): %.0f\n', sampleRate_Hz);
    fprintf('Pulse width (s): %.6f\n', pulseWidth_s);
    disp('Pulse times (s):');
    disp(pulseTimes_s.');
    fprintf('Total duration (s): %.6f\n', totalDuration_s);
    fprintf('Total samples: %d\n', totalSamples);
    fprintf('High samples: %d\n', nnz(outputWaveform));

    do_task.start();

    if isempty(startTrigger)
        disp('Task started immediately.');
    else
        disp('Task armed and waiting for external trigger.');
    end
    fprintf('Task handle saved to base workspace as %s\n', taskVarName);
end
