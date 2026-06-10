function hFig = affineCalibrationGui(varargin)
%AFFINECALIBRATIONGUI GUI for sequential single-hole affine calibration.
%   This GUI is intentionally built with classic MATLAB figure/uicontrol
%   widgets so it stays compatible with the ScanImage/most GUI stack.
%
%   Name/value pairs:
%       'PatternRoiPath'
%       'InitialTransform'
%       'OutputDir'
%       'State'
%       'ApplyTransformFcn'
%       'BurnSingleHoleFcn'
%       'AcquireBeforePassFcn'
%       'AcquireAfterPassFcn'
%       'DetectHolesFcn'
%       'MoveAfterIterationFcn'
%       'AutoStageStepUm'
%       'SequentialHolePauseSeconds'
%       'UseFullAffineCorrection'
%       'UseScaleCorrection'
%
%   The GUI assumes each entry in the pattern ROI should be burned individually.
%   A "pass" means iterating through those entries one-by-one, then acquiring
%   and fitting from the resulting burn pattern.

parser = inputParser();
parser.addParameter('PatternRoiPath', 'affine_test_pattern.roi', @(x) ischar(x) || isstring(x));
parser.addParameter('InitialTransform', eye(3), @(x) isnumeric(x) && isequal(size(x), [3 3]));
parser.addParameter('OutputDir', pwd, @(x) ischar(x) || isstring(x));
parser.addParameter('State', struct(), @isstruct);
parser.addParameter('ApplyTransformFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('BurnSingleHoleFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('AcquireBeforePassFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('AcquireAfterPassFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('DetectHolesFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('MoveAfterIterationFcn', [], @(x) isempty(x) || isa(x, 'function_handle'));
parser.addParameter('AutoStageStepUm', [50 50], @(x) isnumeric(x) && numel(x) == 2 && all(isfinite(x)));
parser.addParameter('SequentialHolePauseSeconds', 0.75, @(x) isnumeric(x) && isscalar(x) && x >= 0);
parser.addParameter('UseFullAffineCorrection', false, @(x) islogical(x) || isnumeric(x));
parser.addParameter('UseScaleCorrection', true, @(x) islogical(x) || isnumeric(x));
parser.parse(varargin{:});
cfg = configureDefaultCallbacks(parser.Results);
cfg.InitialTransformWasProvided = ~any(strcmpi(parser.UsingDefaults, 'InitialTransform'));
if ~cfg.InitialTransformWasProvided
    cfg.InitialTransform = readInitialTransformFromScanImage(cfg);
end

ui = struct();
ui.stopRequested = false;
ui.selectedHoleIndex = 1;
ui.lastResult = [];
ui.currentPatternCenters = zeros(0, 2);
ui.currentPatternNames = {};

hFig = figure( ...
    'Name', 'Sequential Affine Calibration', ...
    'NumberTitle', 'off', ...
    'MenuBar', 'none', ...
    'ToolBar', 'none', ...
    'HandleVisibility', 'callback', ...
    'Position', [120 80 1200 760], ...
    'Color', get(0, 'defaultUicontrolBackgroundColor'));

mainPanel = uipanel('Parent', hFig, 'Units', 'normalized', 'Position', [0 0 1 1]);

uicontrol(mainPanel, 'Style', 'text', 'String', 'Pattern ROI', ...
    'HorizontalAlignment', 'left', 'Units', 'normalized', 'Position', [0.02 0.94 0.08 0.03]);
ui.patternEdit = uicontrol(mainPanel, 'Style', 'edit', 'String', char(cfg.PatternRoiPath), ...
    'HorizontalAlignment', 'left', 'Units', 'normalized', 'Position', [0.10 0.94 0.46 0.04]);
ui.loadPatternButton = uicontrol(mainPanel, 'Style', 'pushbutton', 'String', 'Load Pattern', ...
    'Units', 'normalized', 'Position', [0.58 0.94 0.10 0.04], 'Callback', @onLoadPattern);

uicontrol(mainPanel, 'Style', 'text', 'String', 'Output Dir', ...
    'HorizontalAlignment', 'left', 'Units', 'normalized', 'Position', [0.02 0.89 0.08 0.03]);
ui.outputDirEdit = uicontrol(mainPanel, 'Style', 'edit', 'String', char(cfg.OutputDir), ...
    'HorizontalAlignment', 'left', 'Units', 'normalized', 'Position', [0.10 0.89 0.46 0.04]);

uicontrol(mainPanel, 'Style', 'text', 'String', 'Max Iter', ...
    'HorizontalAlignment', 'left', 'Units', 'normalized', 'Position', [0.70 0.94 0.06 0.03]);
ui.maxIterationsEdit = uicontrol(mainPanel, 'Style', 'edit', 'String', '5', ...
    'Units', 'normalized', 'Position', [0.76 0.94 0.05 0.04]);
uicontrol(mainPanel, 'Style', 'text', 'String', 'Min Improve (um)', ...
    'HorizontalAlignment', 'left', 'Units', 'normalized', 'Position', [0.82 0.94 0.10 0.03]);
ui.minImproveEdit = uicontrol(mainPanel, 'Style', 'edit', 'String', '1', ...
    'Units', 'normalized', 'Position', [0.92 0.94 0.05 0.04]);

uicontrol(mainPanel, 'Style', 'text', 'String', 'Max Match (um)', ...
    'HorizontalAlignment', 'left', 'Units', 'normalized', 'Position', [0.70 0.89 0.09 0.03]);
ui.maxMatchEdit = uicontrol(mainPanel, 'Style', 'edit', 'String', 'Inf', ...
    'Units', 'normalized', 'Position', [0.79 0.89 0.08 0.04]);
uicontrol(mainPanel, 'Style', 'text', 'String', 'Min Matches', ...
    'HorizontalAlignment', 'left', 'Units', 'normalized', 'Position', [0.88 0.89 0.08 0.03]);
ui.minMatchesEdit = uicontrol(mainPanel, 'Style', 'edit', 'String', '3', ...
    'Units', 'normalized', 'Position', [0.96 0.89 0.03 0.04]);

ui.holeTable = uitable('Parent', mainPanel, ...
    'Units', 'normalized', ...
    'Position', [0.02 0.46 0.47 0.38], ...
    'ColumnName', {'Hole', 'Name', 'Scanner X', 'Scanner Y', 'Status'}, ...
    'ColumnEditable', [false false false false false], ...
    'CellSelectionCallback', @onHoleSelected, ...
    'Data', cell(0, 5));

ui.iterationTable = uitable('Parent', mainPanel, ...
    'Units', 'normalized', ...
    'Position', [0.51 0.46 0.47 0.38], ...
    'ColumnName', {'Iter', 'Matches', 'Measured RMS', 'Predicted RMS', 'Confirmed Improve', 'Accepted'}, ...
    'ColumnEditable', [false false false false false false], ...
    'Data', cell(0, 6));

ui.logBox = uicontrol(mainPanel, 'Style', 'listbox', ...
    'Units', 'normalized', ...
    'Position', [0.02 0.06 0.67 0.34], ...
    'String', {'Sequential affine calibration GUI ready.'}, ...
    'Max', 2, 'Min', 0);

ui.statusText = uicontrol(mainPanel, 'Style', 'text', 'String', 'Idle', ...
    'HorizontalAlignment', 'left', 'Units', 'normalized', 'Position', [0.02 0.01 0.67 0.03]);

ui.applyTransformButton = uicontrol(mainPanel, 'Style', 'pushbutton', 'String', 'Apply Initial Transform', ...
    'Units', 'normalized', 'Position', [0.72 0.36 0.23 0.05], 'Callback', @onApplyTransform);
ui.burnSelectedButton = uicontrol(mainPanel, 'Style', 'pushbutton', 'String', 'Burn Selected Hole', ...
    'Units', 'normalized', 'Position', [0.72 0.29 0.23 0.05], 'Callback', @onBurnSelectedHole);
ui.burnPassButton = uicontrol(mainPanel, 'Style', 'pushbutton', 'String', 'Burn One Full Pass', ...
    'Units', 'normalized', 'Position', [0.72 0.22 0.23 0.05], 'Callback', @onBurnFullPass);
ui.runOneIterButton = uicontrol(mainPanel, 'Style', 'pushbutton', 'String', 'Run One Calibration Iteration', ...
    'Units', 'normalized', 'Position', [0.72 0.15 0.23 0.05], 'Callback', @onRunSingleIteration);
ui.runAutoButton = uicontrol(mainPanel, 'Style', 'pushbutton', 'String', 'Run Auto Loop', ...
    'Units', 'normalized', 'Position', [0.72 0.08 0.11 0.05], 'Callback', @onRunAuto);
ui.stopButton = uicontrol(mainPanel, 'Style', 'pushbutton', 'String', 'Stop', ...
    'Units', 'normalized', 'Position', [0.84 0.08 0.11 0.05], 'Callback', @onStop);

guidata(hFig, struct('cfg', cfg, 'ui', ui));
onLoadPattern();

    function onLoadPattern(~, ~)
        data = guidata(hFig);
        try
            patternPath = get(data.ui.patternEdit, 'String');
            [centersXY, names] = extractStimulusCenters(patternPath);
            data.ui.currentPatternCenters = centersXY;
            data.ui.currentPatternNames = names;
            data.ui.selectedHoleIndex = min(max(1, data.ui.selectedHoleIndex), size(centersXY, 1));
            tableData = cell(size(centersXY, 1), 5);
            for rowIndex = 1:size(centersXY, 1)
                tableData{rowIndex, 1} = rowIndex;
                tableData{rowIndex, 2} = names{rowIndex};
                tableData{rowIndex, 3} = centersXY(rowIndex, 1);
                tableData{rowIndex, 4} = centersXY(rowIndex, 2);
                tableData{rowIndex, 5} = 'pending';
            end
            set(data.ui.holeTable, 'Data', tableData);
            setStatus('Pattern loaded.');
            logMessage(sprintf('Loaded %d hole(s) from %s', size(centersXY, 1), patternPath));
            guidata(hFig, data);
        catch ME
            handleFailure(ME);
        end
    end

    function onHoleSelected(~, event)
        data = guidata(hFig);
        if ~isempty(event.Indices)
            data.ui.selectedHoleIndex = event.Indices(1);
            guidata(hFig, data);
        end
    end

    function onApplyTransform(~, ~)
        data = guidata(hFig);
        try
            if isempty(data.cfg.ApplyTransformFcn)
                error('No ApplyTransformFcn configured for this GUI.');
            end
            data.cfg.ApplyTransformFcn(double(data.cfg.InitialTransform), data.cfg.State);
            logMessage('Applied initial transform.');
            setStatus('Initial transform applied.');
        catch ME
            handleFailure(ME);
        end
    end

    function onBurnSelectedHole(~, ~)
        data = guidata(hFig);
        try
            ensureBurnCallbackConfigured(data.cfg);
            patternPath = get(data.ui.patternEdit, 'String');
            roiIndex = data.ui.selectedHoleIndex;
            roiGroup = buildSingleStimulusRoiGroup(patternPath, roiIndex);
            singleDir = fullfile(get(data.ui.outputDirEdit, 'String'), 'manual_single_hole');
            if exist(singleDir, 'dir') ~= 7
                mkdir(singleDir);
            end
            singleRoiPath = fullfile(singleDir, sprintf('hole_%03d.roi', roiIndex));
            roiGroup.saveToFile(singleRoiPath);
            holeState = data.cfg.State;
            holeState.roiIndex = roiIndex;
            holeState.singleRoiPath = singleRoiPath;
            holeState.outputDir = singleDir;
            setStatus(sprintf('Burning hole %d...', roiIndex));
            data.cfg.BurnSingleHoleFcn(singleRoiPath, roiGroup, holeState);
            updateHoleStatus(roiIndex, 'burned');
            logMessage(sprintf('Burned hole %d.', roiIndex));
            setStatus('Selected hole burn complete.');
        catch ME
            handleFailure(ME);
        end
    end

    function onBurnFullPass(~, ~)
        data = guidata(hFig);
        try
            ensureSequentialCallbacksConfigured(data.cfg);
            setStatus('Running one sequential burn pass...');
            tiffPath = burnStimulusPatternSequentially( ...
                get(data.ui.patternEdit, 'String'), ...
                'BurnSingleHoleFcn', data.cfg.BurnSingleHoleFcn, ...
                'AcquireBeforePassFcn', data.cfg.AcquireBeforePassFcn, ...
                'AcquireAfterPassFcn', data.cfg.AcquireAfterPassFcn, ...
                'PauseSeconds', data.cfg.SequentialHolePauseSeconds, ...
                'OutputDir', fullfile(get(data.ui.outputDirEdit, 'String'), 'manual_full_pass'), ...
                'State', data.cfg.State, ...
                'ProgressFcn', @progressCallback, ...
                'StopCheckFcn', @stopRequested);
            logMessage(sprintf('Full pass completed. TIFF: %s', tiffPath));
            detectedXY = data.cfg.DetectHolesFcn(tiffPath, makeDetectionState(data, tiffPath, 'manual_full_pass'));
            logMessage(sprintf('Detected %d burn hole(s).', size(detectedXY, 1)));
            setStatus('Full pass complete.');
        catch ME
            handleFailure(ME);
        end
    end

    function onRunSingleIteration(~, ~)
        runCalibrationWithMaxIterations(1);
    end

    function onRunAuto(~, ~)
        runCalibrationWithMaxIterations(parseNumericField(ui.maxIterationsEdit, 5));
    end

    function runCalibrationWithMaxIterations(maxIterations)
        data = guidata(hFig);
        try
            ensureSequentialCallbacksConfigured(data.cfg);
            ensureDetectCallbackConfigured(data.cfg);
            data.ui.stopRequested = false;
            guidata(hFig, data);
            setStatus('Running sequential affine calibration...');

            options = struct();
            if ~data.cfg.InitialTransformWasProvided
                data.cfg.InitialTransform = readInitialTransformFromScanImage(data.cfg);
                guidata(hFig, data);
                logMessage('Using current ScanImage photostim scanner transform as calibration baseline.');
            end
            options.InitialTransform = double(data.cfg.InitialTransform);
            options.OutputDir = get(data.ui.outputDirEdit, 'String');
            options.MaxIterations = maxIterations;
            options.MinImprovementUm = parseNumericField(ui.minImproveEdit, 1);
            options.MaxMatchDistanceUm = parseNumericField(ui.maxMatchEdit, inf);
            options.MinMatchedPoints = parseNumericField(ui.minMatchesEdit, 3);
            options.UseFullAffine = logical(data.cfg.UseFullAffineCorrection);
            options.UseScaleCorrection = logical(data.cfg.UseScaleCorrection);
            options.ApplyTransformFcn = data.cfg.ApplyTransformFcn;
            options.DetectHolesFcn = data.cfg.DetectHolesFcn;
            options.State = data.cfg.State;
            if maxIterations > 1
                options.MoveAfterIterationFcn = data.cfg.MoveAfterIterationFcn;
            end
            options.BurnAndAcquireFcn = @(iterStimRoiPath, iterState) burnStimulusPatternSequentially( ...
                iterStimRoiPath, ...
                'BurnSingleHoleFcn', data.cfg.BurnSingleHoleFcn, ...
                'AcquireBeforePassFcn', data.cfg.AcquireBeforePassFcn, ...
                'AcquireAfterPassFcn', data.cfg.AcquireAfterPassFcn, ...
                'PauseSeconds', data.cfg.SequentialHolePauseSeconds, ...
                'OutputDir', fullfile(iterState.iterationDir, 'sequential_burn'), ...
                'State', iterState, ...
                'ProgressFcn', @progressCallback, ...
                'StopCheckFcn', @stopRequested);

            result = runIterativeAffineCalibration(get(data.ui.patternEdit, 'String'), options);
            data = guidata(hFig);
            data.ui.lastResult = result;
            data.cfg.InitialTransform = result.bestTransform;
            guidata(hFig, data);
            refreshIterationTable(result);
            logMessage(sprintf('Calibration finished. Best iteration: %d', result.bestIteration));
            setStatus('Calibration finished.');
        catch ME
            handleFailure(ME);
        end
    end

    function onStop(~, ~)
        data = guidata(hFig);
        data.ui.stopRequested = true;
        guidata(hFig, data);
        logMessage('Stop requested.');
        setStatus('Stop requested.');
    end

    function progressCallback(eventName, eventData)
        switch eventName
            case 'startPass'
                logMessage(sprintf('Starting sequential pass with %d hole(s).', numel(eventData.roiIndices)));
            case 'startHole'
                updateHoleStatus(eventData.roiIndex, 'burning');
                logMessage(sprintf('Burning hole %d/%d', eventData.positionIndex, eventData.numHoles));
            case 'endHole'
                updateHoleStatus(eventData.roiIndex, 'burned');
            case 'acquireBeforePass'
                logMessage('Acquiring pre-burn baseline TIFF...');
            case 'beforePassAcquired'
                logMessage(sprintf('Pre-burn TIFF ready: %s', eventData.preBurnTiffPath));
            case 'acquireAfterPass'
                logMessage('Sequential pass finished. Acquiring post-burn TIFF...');
            case 'endPass'
                logMessage(sprintf('Post-pass TIFF ready: %s', eventData.tiffPath));
        end
        drawnow();
    end

    function tf = stopRequested()
        data = guidata(hFig);
        tf = logical(data.ui.stopRequested);
    end

    function updateHoleStatus(roiIndex, statusText)
        data = guidata(hFig);
        tableData = get(data.ui.holeTable, 'Data');
        if ~isempty(tableData) && roiIndex <= size(tableData, 1)
            tableData{roiIndex, 5} = statusText;
            set(data.ui.holeTable, 'Data', tableData);
        end
        drawnow();
    end

    function refreshIterationTable(result)
        tableData = cell(numel(result.iterations), 6);
        for rowIndex = 1:numel(result.iterations)
            iterRecord = result.iterations(rowIndex);
            tableData{rowIndex, 1} = iterRecord.iteration;
            tableData{rowIndex, 2} = size(iterRecord.matchPairs, 1);
            tableData{rowIndex, 3} = iterRecord.rmsBeforeUm;
            tableData{rowIndex, 4} = iterRecord.rmsAfterUm;
            tableData{rowIndex, 5} = iterRecord.improvementUm;
            tableData{rowIndex, 6} = iterRecord.accepted;
        end
        set(ui.iterationTable, 'Data', tableData);
    end

    function logMessage(message)
        data = guidata(hFig);
        currentLines = get(data.ui.logBox, 'String');
        timestamp = char(datetime('now', 'Format', 'HH:mm:ss'));
        currentLines{end + 1, 1} = sprintf('[%s] %s', timestamp, message);
        set(data.ui.logBox, 'String', currentLines, 'Value', numel(currentLines));
    end

    function setStatus(message)
        set(ui.statusText, 'String', message);
        drawnow();
    end

    function value = parseNumericField(handleObj, defaultValue)
        textValue = strtrim(get(handleObj, 'String'));
        if strcmpi(textValue, 'inf')
            value = inf;
            return;
        end
        value = str2double(textValue);
        if ~isfinite(value)
            value = defaultValue;
        end
    end

    function ensureBurnCallbackConfigured(localCfg)
        if isempty(localCfg.BurnSingleHoleFcn)
            error('No BurnSingleHoleFcn configured. Provide a callback for single-hole stimulation.');
        end
    end

    function ensureSequentialCallbacksConfigured(localCfg)
        ensureBurnCallbackConfigured(localCfg);
        if isempty(localCfg.AcquireAfterPassFcn)
            error('No AcquireAfterPassFcn configured. Provide a callback that returns the post-pass TIFF path.');
        end
        if isempty(localCfg.ApplyTransformFcn)
            error('No ApplyTransformFcn configured. Provide a callback that writes the current transform to ScanImage.');
        end
    end

    function ensureDetectCallbackConfigured(localCfg)
        if isempty(localCfg.DetectHolesFcn)
            error('No DetectHolesFcn configured. Provide a callback that returns detected hole centers in reference-space microns.');
        end
    end

    function localCfg = configureDefaultCallbacks(localCfg)
        if isempty(localCfg.BurnSingleHoleFcn)
            localCfg.BurnSingleHoleFcn = @defaultBurnSingleHole;
        end
        if isempty(localCfg.ApplyTransformFcn)
            localCfg.ApplyTransformFcn = @defaultApplyTransform;
        end
        if isempty(localCfg.AcquireBeforePassFcn)
            localCfg.AcquireBeforePassFcn = @defaultAcquireBeforePass;
        end
        if isempty(localCfg.AcquireAfterPassFcn)
            localCfg.AcquireAfterPassFcn = @defaultAcquireAfterPass;
        end
        if isempty(localCfg.DetectHolesFcn)
            localCfg.DetectHolesFcn = @defaultDetectHoles;
        end
        if isempty(localCfg.MoveAfterIterationFcn)
            localCfg.MoveAfterIterationFcn = @defaultMoveAfterIteration;
        end
    end

    function defaultBurnSingleHole(singleRoiPath, ~, localState)
        hSiObj = resolveScanImageHandle(localState);
        triggerPhotostimPattern(hSiObj, singleRoiPath, 'UseOnDemand', false);
    end

    function defaultApplyTransform(transformMatrix, localState)
        hSiObj = resolveScanImageHandle(localState);
        setPhotostimScannerTransform(hSiObj, transformMatrix);
    end

    function transformMatrix = readInitialTransformFromScanImage(localCfg)
        try
            hSiObj = resolveScanImageHandle(localCfg.State);
            transformMatrix = readPhotostimScannerTransform(hSiObj);
        catch ME
            transformMatrix = eye(3);
            try
                logMessage(sprintf('Could not read live photostim transform; using identity. %s', ME.message));
            catch
            end
        end
    end

    function transformMatrix = readPhotostimScannerTransform(hSiObj)
        if isprop(hSiObj, 'hScan_RGG_P2') && ~isempty(hSiObj.hScan_RGG_P2) ...
                && isprop(hSiObj.hScan_RGG_P2, 'scannerToRefTransform')
            transformMatrix = double(hSiObj.hScan_RGG_P2.scannerToRefTransform);
        elseif isprop(hSiObj, 'hPhotostim') && ~isempty(hSiObj.hPhotostim) ...
                && isprop(hSiObj.hPhotostim, 'hScan') && ~isempty(hSiObj.hPhotostim.hScan) ...
                && isprop(hSiObj.hPhotostim.hScan, 'scannerToRefTransform')
            transformMatrix = double(hSiObj.hPhotostim.hScan.scannerToRefTransform);
        else
            error(['Could not locate hSI.hScan_RGG_P2.scannerToRefTransform ' ...
                'or hSI.hPhotostim.hScan.scannerToRefTransform.']);
        end
        validateattributes(transformMatrix, {'numeric'}, {'size', [3 3], 'finite', 'real'}, ...
            mfilename, 'scannerToRefTransform');
    end

    function tiffPath = defaultAcquireAfterPass(passState)
        hSiObj = resolveScanImageHandle(passState);
        tiffPath = acquirePostBurnMeanTiff(hSiObj, passState.outputDir, ...
            'NumFrames', 100, ...
            'LogStem', 'post_burn');
    end

    function tiffPath = defaultAcquireBeforePass(passState)
        hSiObj = resolveScanImageHandle(passState);
        tiffPath = acquirePostBurnMeanTiff(hSiObj, passState.outputDir, ...
            'NumFrames', 100, ...
            'LogStem', 'pre_burn');
    end

    function detectedXY = defaultDetectHoles(tiffPath, detectState)
        patternPath = '';
        baselinePath = '';
        if isstruct(detectState) && isfield(detectState, 'patternRoiPath')
            patternPath = detectState.patternRoiPath;
        end
        if isstruct(detectState) && isfield(detectState, 'preBurnTiffPath')
            baselinePath = detectState.preBurnTiffPath;
        end
        if isempty(baselinePath)
            baselinePath = resolveSiblingPreBurnTiffPath(tiffPath);
        end
        detectedXY = detectBurnHolesFromTiff(tiffPath, ...
            'PatternRoiPath', patternPath, ...
            'BaselineTiffPath', baselinePath, ...
            'WriteArtifacts', true);
    end

    function defaultMoveAfterIteration(iterState)
        hSiObj = resolveScanImageHandle(iterState);
        assert(isprop(hSiObj, 'hMotors') && ~isempty(hSiObj.hMotors), ...
            'ScanImage handle does not expose hMotors for auto-stage stepping.');

        hMotors = hSiObj.hMotors;
        hMotors.queryPosition();
        beforePosition = double(hMotors.samplePosition);
        validateattributes(beforePosition, {'numeric'}, {'vector', 'numel', 3, 'finite', 'real'}, ...
            mfilename, 'hSI.hMotors.samplePosition');

        stepUm = double(cfg.AutoStageStepUm(:).');
        targetPosition = beforePosition;
        targetPosition(1:2) = targetPosition(1:2) + stepUm;

        logMessage(sprintf('Auto loop moving stage by [%.3g %.3g] um before iteration %d.', ...
            stepUm(1), stepUm(2), iterState.nextIteration));
        hMotors.moveSample(targetPosition, false);
        hMotors.queryPosition();
        movedPosition = double(hMotors.samplePosition);

        hMotors.setRelativeZero([0 0 0]);
        hMotors.queryPosition();
        zeroedPosition = double(hMotors.samplePosition);

        logMessage(sprintf(['Stage moved/zeroed. Before=[%.3f %.3f %.3f], ' ...
            'moved=[%.3f %.3f %.3f], zeroed=[%.3f %.3f %.3f].'], ...
            beforePosition(1), beforePosition(2), beforePosition(3), ...
            movedPosition(1), movedPosition(2), movedPosition(3), ...
            zeroedPosition(1), zeroedPosition(2), zeroedPosition(3)));
    end

    function detectState = makeDetectionState(data, tiffPath, outputSubdir)
        detectState = data.cfg.State;
        detectState.patternRoiPath = get(data.ui.patternEdit, 'String');
        detectState.tiffPath = tiffPath;
        detectState.outputDir = fullfile(get(data.ui.outputDirEdit, 'String'), outputSubdir);
        detectState.preBurnTiffPath = resolvePreBurnTiffPath(detectState.outputDir);
    end

    function preBurnPath = resolvePreBurnTiffPath(outputDir)
        preBurnPath = '';
        files = dir(fullfile(outputDir, 'pre_burn_mean.tif'));
        if isempty(files)
            return;
        end
        preBurnPath = fullfile(files(end).folder, files(end).name);
    end

    function preBurnPath = resolveSiblingPreBurnTiffPath(tiffPath)
        [folderPath, ~] = fileparts(tiffPath);
        preBurnPath = resolvePreBurnTiffPath(folderPath);
    end

    function hSiObj = resolveScanImageHandle(localState)
        if isstruct(localState) && isfield(localState, 'hSI') && ~isempty(localState.hSI)
            hSiObj = localState.hSI;
        else
            global hSI
            hSiObj = hSI;
        end
        assert(~isempty(hSiObj), ...
            ['No ScanImage handle found. Launch ScanImage first so global hSI exists, ' ...
             'or pass State.hSI when creating affineCalibrationGui.']);
    end

    function handleFailure(ME)
        logMessage(['ERROR: ' ME.message]);
        setStatus('Error. See log.');
        rethrow(ME);
    end
end
