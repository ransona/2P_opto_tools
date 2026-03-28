function results = debugFirstStimStrategies(varargin)
% debugFirstStimStrategies
% Probe how the first stimulus in a photostim sequence actually advances on
% this ScanImage build.
%
% This is intended to debug the "leading park did not advance" path by
% trying a small set of start/trigger strategies against the same prepared
% stimulus groups and logging photostim state before and after each action.
%
% Usage:
%   results = opto.scanimage.debugFirstStimStrategies();
%   results = opto.scanimage.debugFirstStimStrategies('triggerSequence',[2 3 4 2]);
%
% Defaults:
% - uses base-workspace hSI
% - uses [2 3 2] when stimulus groups 2 and 3 exist
% - waits through several settle windows after each strategy

    p = inputParser();
    addParameter(p, 'hsiVar', 'hSI');
    addParameter(p, 'triggerSequence', []);
    addParameter(p, 'settleTimes_s', [0.01 0.05 0.1 0.25 0.5 1.0 2.0 5.0]);
    parse(p, varargin{:});

    hsiVar = char(string(p.Results.hsiVar));
    triggerSequence = double(p.Results.triggerSequence(:).');
    settleTimes_s = double(p.Results.settleTimes_s(:).');

    assert(evalin('base', sprintf("exist('%s','var')", hsiVar)) == 1, ...
        'Base-workspace variable %s was not found.', hsiVar);
    hSI = evalin('base', hsiVar);
    assert(~isempty(hSI) && isprop(hSI, 'hPhotostim') && ~isempty(hSI.hPhotostim), ...
        'ScanImage photostim handle is not available.');
    hPs = hSI.hPhotostim;

    if isempty(triggerSequence)
        assert(numel(hPs.stimRoiGroups) >= 3, ...
            'Need at least stimulus groups 1, 2, 3 prepared to use the default trigger sequence.');
        triggerSequence = [2 3 2];
    end

    assert(~isempty(triggerSequence), 'triggerSequence must not be empty.');
    assert(max(triggerSequence) <= numel(hPs.stimRoiGroups), ...
        'triggerSequence references stimulus group(s) that are not prepared.');

    oldStimImmediately = [];
    if isprop(hPs, 'stimImmediately')
        oldStimImmediately = logical(hPs.stimImmediately);
    end

    disp('----------');
    disp('debugFirstStimStrategies');
    disp('Trigger sequence under test:');
    disp(triggerSequence);
    disp('Prepared stimulus group count:');
    disp(numel(hPs.stimRoiGroups));
    disp('Settle times (s):');
    disp(settleTimes_s);
    logState(hPs, 'INITIAL_STATE');

    strategies = {
        struct('name', 'stimImmediately_on_start', 'stimImmediately', true,  'triggerDelay_s', NaN)
        struct('name', 'manual_trigger_immediate', 'stimImmediately', false, 'triggerDelay_s', 0.0)
        struct('name', 'manual_trigger_after_010ms', 'stimImmediately', false, 'triggerDelay_s', 0.01)
        struct('name', 'manual_trigger_after_050ms', 'stimImmediately', false, 'triggerDelay_s', 0.05)
        struct('name', 'manual_trigger_after_100ms', 'stimImmediately', false, 'triggerDelay_s', 0.1)
        struct('name', 'manual_trigger_after_250ms', 'stimImmediately', false, 'triggerDelay_s', 0.25)
        struct('name', 'manual_trigger_after_500ms', 'stimImmediately', false, 'triggerDelay_s', 0.5)
    };

    results = struct( ...
        'name', cell(numel(strategies), 1), ...
        'baselinePosition', NaN, ...
        'baselineCompletedSequences', NaN, ...
        'finalPosition', NaN, ...
        'finalCompletedSequences', NaN, ...
        'advanced', false, ...
        'completed', false, ...
        'error', "" ...
    );

    cleanupObj = onCleanup(@() restorePhotostim(hPs, oldStimImmediately));

    for idx = 1:numel(strategies)
        strategy = strategies{idx};
        disp('----------');
        fprintf('Strategy: %s\n', strategy.name);

        results(idx).name = strategy.name;
        results(idx).error = "";
        try
            if hPs.active
                disp('Aborting active photostim before strategy.');
                hPs.abort();
                drawnow();
                pause(0.25);
            end

            hPs.stimulusMode = 'sequence';
            hPs.sequenceSelectedStimuli = triggerSequence;
            hPs.numSequences = 1;
            if ~isempty(oldStimImmediately)
                hPs.stimImmediately = logical(strategy.stimImmediately);
            end

            logState(hPs, 'PRE_START_STATE');
            hPs.start();
            drawnow();
            logState(hPs, 'POST_START_STATE');

            baseline = readState(hPs);
            results(idx).baselinePosition = baseline.position;
            results(idx).baselineCompletedSequences = baseline.completedSequences;

            if ~isnan(strategy.triggerDelay_s)
                fprintf('Waiting %.3f s before triggerStim().\n', strategy.triggerDelay_s);
                pause(strategy.triggerDelay_s);
                drawnow();
                logState(hPs, 'PRE_TRIGGER_STATE');
                hPs.triggerStim();
                drawnow();
                disp('triggerStim() called.');
                logState(hPs, 'POST_TRIGGER_STATE');
            end

            for settleTime_s = settleTimes_s
                pause(settleTime_s);
                drawnow();
                logState(hPs, sprintf('SETTLED_%04dms', round(1000 * settleTime_s)));
            end

            finalState = readState(hPs);
            results(idx).finalPosition = finalState.position;
            results(idx).finalCompletedSequences = finalState.completedSequences;
            results(idx).advanced = ...
                ~isnan(results(idx).baselinePosition) && ~isnan(results(idx).finalPosition) && ...
                results(idx).finalPosition > results(idx).baselinePosition;
            results(idx).completed = ...
                ~isnan(results(idx).baselineCompletedSequences) && ~isnan(results(idx).finalCompletedSequences) && ...
                results(idx).finalCompletedSequences > results(idx).baselineCompletedSequences;

            fprintf('SUMMARY %s\n', strategy.name);
            fprintf('  baseline position: %s\n', numericText(results(idx).baselinePosition));
            fprintf('  final position: %s\n', numericText(results(idx).finalPosition));
            fprintf('  baseline completedSequences: %s\n', numericText(results(idx).baselineCompletedSequences));
            fprintf('  final completedSequences: %s\n', numericText(results(idx).finalCompletedSequences));
            fprintf('  advanced: %d\n', results(idx).advanced);
            fprintf('  completed: %d\n', results(idx).completed);
        catch ME
            results(idx).error = string(ME.message);
            fprintf('STRATEGY_ERROR %s\n', strategy.name);
            fprintf('%s\n', ME.getReport());
        end
    end

    disp('----------');
    disp('debugFirstStimStrategies summary');
    for idx = 1:numel(results)
        fprintf('%s | advanced=%d | completed=%d | baseline=%s | final=%s', ...
            results(idx).name, ...
            logical(results(idx).advanced), ...
            logical(results(idx).completed), ...
            numericText(results(idx).baselinePosition), ...
            numericText(results(idx).finalPosition));
        if strlength(results(idx).error) > 0
            fprintf(' | error=%s', results(idx).error);
        end
        fprintf('\n');
    end

    clear cleanupObj;
    restorePhotostim(hPs, oldStimImmediately);
end

function restorePhotostim(hPs, oldStimImmediately)
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
    if ~isempty(oldStimImmediately)
        try
            hPs.stimImmediately = oldStimImmediately;
        catch
        end
    end
end

function state = readState(hPs)
    state = struct();
    state.active = logical(hPs.active);
    state.position = NaN;
    state.completedSequences = NaN;
    state.sequence = [];
    if ~isempty(hPs.sequencePosition)
        state.position = double(hPs.sequencePosition);
    end
    if ~isempty(hPs.completedSequences)
        state.completedSequences = double(hPs.completedSequences);
    end
    if ~isempty(hPs.sequenceSelectedStimuli)
        state.sequence = double(hPs.sequenceSelectedStimuli(:).');
    end
end

function logState(hPs, label)
    state = readState(hPs);
    fprintf('%s active=%d sequencePosition=%s completedSequences=%s\n', ...
        label, state.active, numericText(state.position), numericText(state.completedSequences));
    if ~isempty(state.sequence)
        fprintf('%s sequence=', label);
        disp(state.sequence);
    end
end

function text = numericText(value)
    if isempty(value) || any(isnan(value))
        text = 'NaN';
    else
        text = num2str(value);
    end
end
