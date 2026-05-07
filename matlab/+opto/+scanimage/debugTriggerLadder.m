function results = debugTriggerLadder(varargin)
% debugTriggerLadder
% Build up from the simplest software-triggered SLM sequence toward the
% current live behavior to isolate where sequence mutation breaks SLM use.
%
% This helper intentionally interrupts the current photostim sequence while
% it runs its staged tests. It restores the original photostim groups and
% sequence configuration before returning.

    p = inputParser();
    addParameter(p, 'hsiVar', 'hSI');
    addParameter(p, 'outputLine', 'D1.6');
    addParameter(p, 'sampleRate_Hz', 2000000);
    addParameter(p, 'pulseWidth_s', 0.1);
    addParameter(p, 'pulseSpacing_s', 1.0);
    parse(p, varargin{:});

    hsiVar = char(string(p.Results.hsiVar));
    outputLine = char(string(p.Results.outputLine));
    sampleRate_Hz = double(p.Results.sampleRate_Hz);
    pulseWidth_s = double(p.Results.pulseWidth_s);
    pulseSpacing_s = double(p.Results.pulseSpacing_s);

    assert(evalin('base', sprintf("exist('%s','var')", hsiVar)) == 1, ...
        'Base-workspace variable %s was not found.', hsiVar);
    hSI = evalin('base', hsiVar);
    assert(~isempty(hSI) && isprop(hSI, 'hPhotostim') && ~isempty(hSI.hPhotostim), ...
        'ScanImage photostim handle is not available.');
    hPs = hSI.hPhotostim;

    oldGroups = hPs.stimRoiGroups;
    oldSequence = hPs.sequenceSelectedStimuli;
    oldNumSequences = hPs.numSequences;
    oldStimImmediately = [];
    if isprop(hPs, 'stimImmediately')
        oldStimImmediately = logical(hPs.stimImmediately);
    end

    cleanupObj = onCleanup(@() restorePhotostimState(hPs, oldGroups, oldSequence, oldNumSequences, oldStimImmediately));

    stimGroupIdx = findFirstSlmStimGroup(hPs);
    fprintf('DEBUG_LADDER_STIM_GROUP %d\n', stimGroupIdx);

    tempParkGroup = makeDebugParkGroup('DEBUG_PARK', pulseSpacing_s * 0.5, getPhotostimBeamCount(hSI));
    tempStimGroup = hPs.stimRoiGroups(stimGroupIdx);

    if hPs.active
        hPs.abort();
        drawnow();
        pause(0.25);
    end

    hPs.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1, 0);
    hPs.stimRoiGroups(end + 1) = tempParkGroup;
    hPs.stimRoiGroups(end + 1) = tempStimGroup;
    hPs.stimulusMode = 'sequence';
    hPs.numSequences = 1;
    if ~isempty(oldStimImmediately)
        hPs.stimImmediately = false;
    end

    results = struct( ...
        'name', {}, ...
        'finalPosition', {}, ...
        'completedSequences', {}, ...
        'error', {} ...
    );

    stageSpecs = {
        struct('name', 'single_manual', 'sequence', [1 2 1], 'manualTriggers', 1, 'rewriteAfterFirst', false, 'waveformPulses', 0)
        struct('name', 'double_manual', 'sequence', [1 2 1 2 1], 'manualTriggers', 2, 'rewriteAfterFirst', false, 'waveformPulses', 0)
        struct('name', 'rewrite_then_manual', 'sequence', [1 2 1], 'manualTriggers', 2, 'rewriteAfterFirst', true, 'waveformPulses', 0)
        struct('name', 'minimal_waveform', 'sequence', [1 2 1 2 1 2 1], 'manualTriggers', 0, 'rewriteAfterFirst', false, 'waveformPulses', 3)
    };

    for idx = 1:numel(stageSpecs)
        spec = stageSpecs{idx};
        results(idx).name = spec.name;
        results(idx).finalPosition = NaN;
        results(idx).completedSequences = NaN;
        results(idx).error = "";
        fprintf('----------\n');
        fprintf('DEBUG_LADDER_STAGE %s\n', spec.name);
        try
            if hPs.active
                hPs.abort();
                drawnow();
                pause(0.25);
            end

            hPs.sequenceSelectedStimuli = double(spec.sequence(:).');
            hPs.start();
            drawnow();
            pause(0.1);
            logCurrentState(hPs, 'STAGE_START');

            if spec.manualTriggers > 0
                for triggerIdx = 1:spec.manualTriggers
                    if triggerIdx == 2 && spec.rewriteAfterFirst
                        hPs.sequenceSelectedStimuli = [double(spec.sequence(:).') 2 1];
                        disp('DEBUG_LADDER_REWRITE_APPLIED');
                        disp(hPs.sequenceSelectedStimuli);
                        drawnow();
                    end
                    hPs.triggerStim();
                    drawnow();
                    pause(pulseSpacing_s * 0.5);
                    logCurrentState(hPs, sprintf('POST_MANUAL_%02d', triggerIdx));
                end
            elseif spec.waveformPulses > 0
                pulseTimes_s = pulseSpacing_s * (1:spec.waveformPulses);
                do_task = opto.scanimage.testVdaqDoTriggeredByDi( ...
                    'outputLine', outputLine, ...
                    'startTrigger', '', ...
                    'sampleRate_Hz', sampleRate_Hz, ...
                    'pulseTimes_s', pulseTimes_s, ...
                    'pulseWidth_s', pulseWidth_s);
                t0 = tic;
                while most.idioms.isValidObj(do_task) && double(do_task.active) && toc(t0) < (max(pulseTimes_s) + 3.0)
                    pause(0.02);
                end
                pause(0.2);
                logCurrentState(hPs, 'POST_WAVEFORM');
            end

            state = readState(hPs);
            results(idx).finalPosition = state.position;
            results(idx).completedSequences = state.completedSequences;
        catch ME
            results(idx).error = string(ME.message);
            fprintf('DEBUG_LADDER_STAGE_ERROR %s\n', spec.name);
            fprintf('%s\n', ME.getReport());
        end
    end

    fprintf('----------\n');
    fprintf('DEBUG_LADDER_SUMMARY\n');
    for idx = 1:numel(results)
        fprintf('%s | finalPosition=%s | completed=%s', ...
            results(idx).name, numericText(results(idx).finalPosition), numericText(results(idx).completedSequences));
        if strlength(results(idx).error) > 0
            fprintf(' | error=%s', results(idx).error);
        end
        fprintf('\n');
    end

    clear cleanupObj;
    restorePhotostimState(hPs, oldGroups, oldSequence, oldNumSequences, oldStimImmediately);
end

function stimGroupIdx = findFirstSlmStimGroup(hPs)
    stimGroupIdx = [];
    for idx = 3:numel(hPs.stimRoiGroups)
        rg = hPs.stimRoiGroups(idx);
        if isempty(rg.rois)
            continue;
        end
        roi = rg.rois(1);
        if isempty(roi.scanfields)
            continue;
        end
        sf = roi.scanfields(1);
        if isprop(sf, 'slmPattern') && ~isempty(sf.slmPattern)
            stimGroupIdx = idx;
            return;
        end
    end
    error('No prepared SLM stimulus group was found in hPs.stimRoiGroups.');
end

function group = makeDebugParkGroup(name, durationSeconds, nBeams)
    group = scanimage.mroi.RoiGroup(char(name));
    sfPoint = scanimage.mroi.scanfield.fields.StimulusField();
    sfPoint.centerXY = [0 0];
    sfPoint.sizeXY = [0 0];
    sfPoint.stimfcnhdl = @scanimage.mroi.stimulusfunctions.point;
    sfPoint.stimparams = {};
    sfPoint.duration = durationSeconds;
    sfPoint.repetitions = 1;
    sfPoint.powers = zeros(1, nBeams);
    roi = scanimage.mroi.Roi();
    roi.add(0, sfPoint);
    group.add(roi);
end

function nBeams = getPhotostimBeamCount(hSI)
    nBeams = 1;
    try
        ss = hSI.hPhotostim.stimScannerset;
        if most.idioms.isValidObj(ss)
            nBeams = numel(ss.beams);
        end
    catch
        nBeams = 1;
    end
end

function restorePhotostimState(hPs, oldGroups, oldSequence, oldNumSequences, oldStimImmediately)
    if isempty(hPs) || ~most.idioms.isValidObj(hPs)
        return;
    end
    try
        if hPs.active
            hPs.abort();
            drawnow();
            pause(0.25);
        end
    catch
    end
    try
        hPs.stimRoiGroups = oldGroups;
        hPs.sequenceSelectedStimuli = oldSequence;
        hPs.numSequences = oldNumSequences;
        if ~isempty(oldStimImmediately)
            hPs.stimImmediately = oldStimImmediately;
        end
        if ~isempty(oldSequence)
            hPs.start();
            drawnow();
            pause(0.1);
        end
    catch
    end
end

function state = readState(hPs)
    state = struct();
    state.active = logical(hPs.active);
    state.position = NaN;
    state.completedSequences = NaN;
    if ~isempty(hPs.sequencePosition)
        state.position = double(hPs.sequencePosition);
    end
    if ~isempty(hPs.completedSequences)
        state.completedSequences = double(hPs.completedSequences);
    end
end

function logCurrentState(hPs, label)
    state = readState(hPs);
    fprintf('%s active=%d sequencePosition=%s completedSequences=%s\n', ...
        label, state.active, numericText(state.position), numericText(state.completedSequences));
    if isempty(hPs.sequenceSelectedStimuli)
        return;
    end
    seq = double(hPs.sequenceSelectedStimuli(:).');
    pos = max(1, min(numel(seq), round(state.position)));
    idx = seq(pos);
    rg = hPs.stimRoiGroups(idx);
    fprintf('%s currentGroup=%d name=%s\n', label, idx, string(rg.name));
    if isempty(rg.rois)
        return;
    end
    roi = rg.rois(1);
    if isempty(roi.scanfields)
        return;
    end
    sf = roi.scanfields(1);
    fprintf('%s stimFcn=%s\n', label, func2str(sf.stimfcnhdl));
    fprintf('%s hasSlmPattern=%d\n', label, double(isprop(sf, 'slmPattern')));
    if isprop(sf, 'slmPattern') && ~isempty(sf.slmPattern)
        disp(size(sf.slmPattern));
    end
end

function text = numericText(value)
    if isempty(value) || any(isnan(value))
        text = 'NaN';
    else
        text = num2str(value);
    end
end
