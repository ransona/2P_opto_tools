classdef SlmSingleCellPulse < most.Gui
    properties (SetObservable)
        centerX_um = 0;
        centerY_um = 0;
        animal_name = 'ESYB';
        spiralDiameter_um = 15;
        power_pct = 20;
        duration_ms = 10;
        pulseCount = 10;
        pulseRate_Hz = 2;
        stimTriggerOnFrame = true;
        forceEnableLoggingForStim = true;
    end

    properties (Hidden)
        revolutions = 5;
        galvoOffsetX_um = 20;
        galvoOffsetY_um = 20;
        prePause_ms = 1;
        postPark_ms = 1;
        preStimImaging_s = 30;
        postStimImaging_s = 30;
        grabFramePadding_s = 15;
    end

    properties (Access = private)
        etStatus;
        hEstimateListeners = event.proplistener.empty(0,1);
        lastSavePath = '';
        lastCellPickerRedImage = [];
        lastCellPickerRedScanfield = [];
        lastCellPickerRedChannel = NaN;
        lastCellPickerRedTimestamp = '';
        hCh1Traces = {};
        hFrameTraceListener = [];
        hFrameTraceTimer = [];
        useIntegrationLogging = false;
        integrationConfigBackup = struct('valid',false);
        traceFramesDoneStart = NaN;
        traceFramesDoneEnd = NaN;
        traceFrameNumberStart = NaN;
        traceFrameNumberEnd = NaN;
        queuedCenters_um = zeros(0,2);
        hCellPickerFig = [];
    end

    methods (Static)
        function h = launch()
            hSI = [];
            hSICtl = [];
            try
                if dabs.resources.ResourceStore.isInstantiated()
                    rs = dabs.resources.ResourceStore();
                    hSI = rs.filterByClass('scanimage.SI');
                    if iscell(hSI)
                        if ~isempty(hSI)
                            hSI = hSI{1};
                        else
                            hSI = [];
                        end
                    end
                end
            catch
                hSI = [];
            end

            if isempty(hSI)
                try
                    if evalin('base','exist(''hSI'',''var'')')
                        hSI = evalin('base','hSI');
                    end
                catch
                    hSI = [];
                end
            end

            if most.idioms.isValidObj(hSI)
                if ~isempty(hSI.hController)
                    hSICtl = hSI.hController{1};
                end
            else
                hSI = [];
            end

            cls = mfilename('class');
            h = feval(cls, hSI, hSICtl);
            h.raise();
        end
    end

    methods
        function obj = SlmSingleCellPulse(hModel, hController)
            if nargin < 1
                hModel = [];
            end
            if nargin < 2
                hController = [];
            end
            obj@most.Gui(hModel, hController, [560 340], 'pixels');
        end

        function delete(obj)
            obj.stopChannel1Logging();
            try
                hPickFigs = findall(0, 'Type', 'figure', 'Tag', 'SlmSingleCellPulsePicker');
                if ~isempty(hPickFigs)
                    delete(hPickFigs);
                end
            catch
            end
            obj.hCellPickerFig = [];
            delete@most.Gui(obj);
        end
    end

    methods (Access = protected)
        function initGui(obj)
            set(obj.hFig,'Name','Single Cell Pulse Stim','Resize','on');

            mainFlow = most.gui.uiflowcontainer('Parent',obj.hFig,'FlowDirection','TopDown');
            panel = uipanel('Parent',mainFlow,'Title','Parameters');
            panelFlow = most.gui.uiflowcontainer('Parent',panel,'FlowDirection','TopDown');

            addRow(panelFlow, 'Center X (um)', obj, 'centerX_um');
            addRow(panelFlow, 'Center Y (um)', obj, 'centerY_um');
            addRow(panelFlow, 'Animal Name', obj, 'animal_name', 'string');
            addRow(panelFlow, 'Spiral Diameter (um)', obj, 'spiralDiameter_um');
            addRow(panelFlow, 'Power (%)', obj, 'power_pct');
            addRow(panelFlow, 'Duration (ms)', obj, 'duration_ms');
            addRow(panelFlow, 'Pulse Count', obj, 'pulseCount');
            addRow(panelFlow, 'Pulse Rate (Hz)', obj, 'pulseRate_Hz');
            addRowCheckbox(panelFlow, 'Trigger On Frame', obj, 'stimTriggerOnFrame');
            addRowCheckbox(panelFlow, 'Enable Logging', obj, 'forceEnableLoggingForStim');

            buttonFlow = most.gui.uiflowcontainer('Parent',mainFlow,'FlowDirection','LeftToRight','HeightLimits',[24 24]);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Pick Cells (Ch2)','Callback',@obj.pickCellFromImage);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Clear Queue','Callback',@obj.clearQueuedCenters);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Stimulate Now','Callback',@obj.stimulateNow);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Stimulate All','Callback',@obj.stimulateAll);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Run PSTH','Callback',@obj.runPsth);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Close','Callback',@(~,~)delete(obj));

            statusFlow = most.gui.uiflowcontainer('Parent',mainFlow,'FlowDirection','LeftToRight','HeightLimits',[18 18]);
            obj.etStatus = obj.addUiControl('Parent',statusFlow,'Style','text','String','Ready','HorizontalAlignment','left');

            obj.hEstimateListeners(end+1) = addlistener(obj, 'forceEnableLoggingForStim', ...
                'PostSet', @(~,~)obj.applyLoggingPreference()); %#ok<AGROW>
            obj.applyLoggingPreference();

            function addRow(parent, label, model, prop, bindingType)
                if nargin < 5 || isempty(bindingType)
                    bindingType = 'value';
                end
                row = most.gui.uiflowcontainer('Parent',parent,'FlowDirection','LeftToRight','HeightLimits',[22 22]);
                most.gui.uicontrol('Parent',row,'Style','text','String',label,'WidthLimits',[130 130], ...
                    'HorizontalAlignment','right');
                most.gui.uicontrol('Parent',row,'Style','edit','Bindings',{model prop bindingType});
            end

            function addRowCheckbox(parent, label, model, prop)
                row = most.gui.uiflowcontainer('Parent',parent,'FlowDirection','LeftToRight','HeightLimits',[22 22]);
                most.gui.uicontrol('Parent',row,'Style','text','String',label,'WidthLimits',[130 130], ...
                    'HorizontalAlignment','right');
                most.gui.uicontrol('Parent',row,'Style','checkbox','Bindings',{model prop 'value'});
            end

        end
    end

    methods
        function ok = stimulateNow(obj, varargin)
            ok = false;
            hSI = [];
            hPhotostim = [];
            startedImaging = false;
            oldStimTriggerTerm = '';
            runCfg = obj.emptyGrabRunConfig();
            plannedSavePath = '';
            saveBaseName = '';
            saveDir = '';
            greenTiffPath = '';
            queueCenterUsed = false;
            queueCountAtStart = 0;

            queueCountAtStart = obj.getQueuedCenterCount();
            if queueCountAtStart > 0
                nextCenter = obj.queuedCenters_um(1,:);
                obj.centerX_um = nextCenter(1);
                obj.centerY_um = nextCenter(2);
                queueCenterUsed = true;
                obj.setStatus(sprintf('Running queued cell 1/%d at (%.3f, %.3f) um...', ...
                    queueCountAtStart, obj.centerX_um, obj.centerY_um));
            end

            try
                hSI = obj.resolveSI();
                hPhotostim = hSI.hPhotostim;
                assert(most.idioms.isValidObj(hPhotostim), 'Photostim component is not available.');
                assert(~obj.isPhotostimActiveSafe(hPhotostim), 'Abort Photostim before triggering.');

                if obj.forceEnableLoggingForStim
                    obj.ensureLoggingForStim(hSI, hPhotostim);
                end
                validateattributes(obj.pulseCount, {'numeric'}, {'scalar','finite','real','nonnan','>=',1});
                validateattributes(obj.pulseRate_Hz, {'numeric'}, {'scalar','finite','real','nonnan','>',0});

                [plannedSavePath, saveBaseName, saveDir] = obj.reserveSessionSavePath();

                try
                    oldStimTriggerTerm = hPhotostim.stimTriggerTerm;
                catch
                    oldStimTriggerTerm = '';
                end
                hPhotostim.stimTriggerTerm = '';

                [idx, stimEntry] = obj.createSingleStimGroup();
                if isempty(idx)
                    obj.restoreStimTriggerTermSafe(hPhotostim, oldStimTriggerTerm);
                    return;
                end

                centers = obj.getCenter();
                obj.startChannel1Logging(hSI, centers);

                if ~obj.isSiActiveSafe(hSI)
                    estRun_s = obj.estimateRunDuration_s(hSI);
                    runCfg = obj.configureGrabForGreenTiff(hSI, saveBaseName, saveDir, estRun_s);
                    startedImaging = runCfg.startedAcq;
                end

                if obj.stimTriggerOnFrame
                    assert(obj.isSiActiveSafe(hSI), 'Trigger On Frame requires active imaging (grab/loop/acquisition).');
                end

                obj.traceFramesDoneStart = obj.getFramesDoneSafe(hSI);
                obj.traceFrameNumberStart = obj.getFrameNumberSafe(hSI);

                nPulses = max(1, round(obj.pulseCount));
                pulsePeriod_s = 1 / max(eps, obj.pulseRate_Hz);
                preStim_s = max(0, localScalarNumeric(obj.preStimImaging_s, 30));

                hPhotostim.stimulusMode = 'sequence';
                hPhotostim.sequenceSelectedStimuli = repmat(idx, 1, nPulses);
                hPhotostim.numSequences = 1;
                hPhotostim.autoTriggerPeriod = 0;
                hPhotostim.stimImmediately = false;
                % Arm photostim right before each trigger so long pre-stim waits
                % do not leave the module idle/disarmed.

                stimTriggerTimes = nan(nPulses,1);
                stimParamList = repmat(stimEntry, nPulses, 1);
                tRun = tic;
                nextPulseEarliest_s = preStim_s;
                for iPulse = 1:nPulses
                    while toc(tRun) < nextPulseEarliest_s
                        pause(min(0.005, nextPulseEarliest_s - toc(tRun)));
                    end

                    if ~obj.isPhotostimActiveSafe(hPhotostim)
                        nRemaining = nPulses - iPulse + 1;
                        hPhotostim.sequenceSelectedStimuli = repmat(idx, 1, nRemaining);
                        hPhotostim.numSequences = 1;
                        hPhotostim.autoTriggerPeriod = 0;
                        hPhotostim.stimImmediately = false;
                        hPhotostim.start();
                        drawnow();
                        okPhotostim = obj.waitForPhotostimActive(hPhotostim, 6);
                        assert(okPhotostim, 'Photostim failed to become active before trigger %d.', iPulse);
                    end

                    if obj.stimTriggerOnFrame
                        okImaging = obj.waitForSiActive(hSI, 2);
                        assert(okImaging, 'Imaging is not active before trigger %d.', iPulse);
                        okFrame = obj.waitForNextFrameEdge(hSI, 2);
                        assert(okFrame, 'Did not observe a new imaging frame before trigger %d.', iPulse);
                    end

                    stimT = [];
                    if ~isempty(obj.hCh1Traces)
                        hTrace = obj.hCh1Traces{1};
                        if ~isempty(hTrace) && isvalid(hTrace)
                            stimT = hTrace.markStim();
                        end
                    end
                    stimCmdT = toc(tRun);
                    hPhotostim.triggerStim();
                    if isempty(stimT)
                        stimT = stimCmdT;
                    end
                    stimTriggerTimes(iPulse,1) = stimT;
                    stimParamList(iPulse,1).pulse_index = iPulse;
                    nextPulseEarliest_s = stimCmdT + pulsePeriod_s;
                end

                tail_s = (obj.prePause_ms + obj.duration_ms + obj.postPark_ms) / 1000 + 0.1;
                if tail_s > 0
                    pause(tail_s);
                end
                postStim_s = max(0, localScalarNumeric(obj.postStimImaging_s, 30));
                if postStim_s > 0
                    pause(postStim_s);
                end

                obj.traceFramesDoneEnd = obj.getFramesDoneSafe(hSI);
                obj.traceFrameNumberEnd = obj.getFrameNumberSafe(hSI);

                obj.stopImagingIfStarted(startedImaging, hSI);
                obj.restoreGrabRunConfig(hSI, runCfg);

                traceDataAll = obj.collectTraceData();
                traceLengthCheck = obj.buildTraceLengthCheck(traceDataAll);
                obj.stopChannel1Logging();

                greenTiffPath = obj.finalizeGreenTiff(runCfg, plannedSavePath);
                [stimTimesToSave, stimTiming] = obj.resolveStimTriggerTimesForSave( ...
                    stimTriggerTimes, greenTiffPath, traceDataAll);
                savePath = obj.saveSessionData(stimTimesToSave, idx, stimParamList, ...
                    traceDataAll, traceLengthCheck, plannedSavePath, greenTiffPath, ...
                    stimTriggerTimes, stimTiming);
                if ~isempty(savePath)
                    obj.lastSavePath = savePath;
                end

                obj.restoreStimTriggerTermSafe(hPhotostim, oldStimTriggerTerm);
                if queueCenterUsed && ~isempty(obj.queuedCenters_um)
                    obj.queuedCenters_um(1,:) = [];
                end
                remainingQueue = obj.getQueuedCenterCount();
                if remainingQueue > 0
                    obj.centerX_um = obj.queuedCenters_um(1,1);
                    obj.centerY_um = obj.queuedCenters_um(1,2);
                end
                if queueCenterUsed
                    obj.setStatus(sprintf('Triggered %d pulses at %.1f Hz. Queue remaining: %d.', ...
                        nPulses, obj.pulseRate_Hz, remainingQueue));
                else
                    obj.setStatus(sprintf('Triggered %d pulses at %.1f Hz.', nPulses, obj.pulseRate_Hz));
                end
                ok = true;
            catch ME
                obj.stopImagingIfStarted(startedImaging, hSI);
                obj.restoreGrabRunConfig(hSI, runCfg);
                obj.stopChannel1Logging();
                obj.restoreStimTriggerTermSafe(hPhotostim, oldStimTriggerTerm);
                if queueCenterUsed
                    obj.setStatus(sprintf('Error: %s (queued cell retained; queue remaining: %d).', ...
                        ME.message, obj.getQueuedCenterCount()));
                else
                    obj.setStatus(sprintf('Error: %s', ME.message));
                end
                most.ErrorHandler.logAndReportError(ME);
            end
        end

        function stimulateAll(obj, varargin)
            nQueuedStart = obj.getQueuedCenterCount();
            if nQueuedStart <= 0
                obj.setStatus('Queue is empty. Pick cells first or use Stimulate Now for current center.');
                return;
            end

            completed = 0;
            while obj.getQueuedCenterCount() > 0
                nBefore = obj.getQueuedCenterCount();
                ok = obj.stimulateNow();
                nAfter = obj.getQueuedCenterCount();

                if ~ok
                    obj.setStatus(sprintf('Stimulate All stopped after %d/%d cells (error on next cell).', ...
                        completed, nQueuedStart));
                    return;
                end

                if nAfter < nBefore
                    completed = completed + (nBefore - nAfter);
                else
                    obj.setStatus(sprintf('Stimulate All stopped after %d/%d cells (no queue progress).', ...
                        completed, nQueuedStart));
                    return;
                end

                drawnow();
            end

            obj.setStatus(sprintf('Stimulate All complete: %d/%d cells.', completed, nQueuedStart));
        end
    end

    methods (Access = private)
        function pickCellFromImage(obj, varargin)
            usedCachedImage = false;
            try
                hSI = obj.resolveSI();
                [img, sf, chNum, usedCachedImage] = obj.getPickerImageAndScanfieldForPicker(hSI);

                hPickFig = figure('Name',sprintf('Cell Picker (Channel %d)', chNum), ...
                    'NumberTitle','off','Color','w', ...
                    'Tag','SlmSingleCellPulsePicker', ...
                    'CloseRequestFcn', @onCancel);
                obj.hCellPickerFig = hPickFig;
                ax = axes('Parent',hPickFig, 'Units','normalized', 'Position',[0.08 0.12 0.84 0.84]);
                imagesc(ax, img);
                axis(ax, 'image');
                set(ax, 'YDir', 'reverse');
                colormap(ax, gray(256));
                title(ax, 'Left click: add point | Click OK to apply + close');
                xlabel(ax, 'X (pixels)');
                ylabel(ax, 'Y (pixels)');
                hold(ax, 'on');

                if ~isempty(obj.queuedCenters_um)
                    try
                        resQ = hSI.objectiveResolution;
                        if isscalar(resQ)
                            queuedRef = obj.queuedCenters_um ./ resQ;
                        else
                            queuedRef = obj.queuedCenters_um ./ resQ(1:2);
                        end
                        queuedPix = scanimage.mroi.util.xformPoints(queuedRef, sf.refToPixelTransform());
                        plot(ax, queuedPix(:,1), queuedPix(:,2), 'go', 'LineWidth', 1.0, 'MarkerSize', 8);
                    catch
                    end
                end

                hPicked = plot(ax, nan, nan, 'rx', 'LineWidth', 1.2, 'MarkerSize', 10);
                setappdata(hPickFig, 'picker_status', 'pending');
                setappdata(hPickFig, 'picker_points', zeros(0,2));
                set(hPickFig, 'WindowButtonDownFcn', @onMouseDown);

                uicontrol('Parent', hPickFig, 'Style', 'pushbutton', 'String', 'OK', ...
                    'Units', 'normalized', 'Position', [0.36 0.01 0.12 0.08], ...
                    'Callback', @onOk);
                uicontrol('Parent', hPickFig, 'Style', 'pushbutton', 'String', 'Cancel', ...
                    'Units', 'normalized', 'Position', [0.52 0.01 0.12 0.08], ...
                    'Callback', @onCancel);

                obj.setStatus('Cell picker open. Left-click to add points, then click OK.');
            catch ME
                obj.setStatus(sprintf('Cell picker failed: %s', ME.message));
                most.ErrorHandler.logAndReportError(ME);
            end

            function onMouseDown(~, ~)
                try
                    if isempty(hPickFig) || ~isvalid(hPickFig)
                        return;
                    end
                    if ~strcmp(get(hPickFig, 'SelectionType'), 'normal')
                        return;
                    end
                    hObj = get(hPickFig, 'CurrentObject');
                    if isempty(hObj)
                        return;
                    end
                    axHit = ancestor(hObj, 'axes');
                    if isempty(axHit) || axHit ~= ax
                        return;
                    end

                    cp = get(ax, 'CurrentPoint');
                    x = cp(1,1);
                    y = cp(1,2);
                    if ~isfinite(x) || ~isfinite(y)
                        return;
                    end

                    xl = xlim(ax);
                    yl = ylim(ax);
                    if x < min(xl) || x > max(xl) || y < min(yl) || y > max(yl)
                        return;
                    end

                    pickedPix = getappdata(hPickFig, 'picker_points');
                    pickedPix(end+1,:) = [x y]; %#ok<AGROW>
                    setappdata(hPickFig, 'picker_points', pickedPix);
                    set(hPicked, 'XData', pickedPix(:,1), 'YData', pickedPix(:,2));
                catch
                end
            end

            function onOk(~, ~)
                try
                    if isempty(hPickFig) || ~isvalid(hPickFig)
                        return;
                    end
                    pickedPix = getappdata(hPickFig, 'picker_points');
                    if isempty(pickedPix)
                        obj.setStatus('Cell picker: no points selected.');
                        closePicker();
                        return;
                    end

                    imgW = size(img,2);
                    imgH = size(img,1);
                    pickedPix(:,1) = min(max(pickedPix(:,1), 1), imgW);
                    pickedPix(:,2) = min(max(pickedPix(:,2), 1), imgH);

                    ptsRef = scanimage.mroi.util.xformPoints(pickedPix, sf.pixelToRefTransform());
                    res = hSI.objectiveResolution;
                    if isscalar(res)
                        centersUm = ptsRef .* res;
                    else
                        centersUm = ptsRef .* res(1:2);
                    end

                    obj.appendQueuedCenters(centersUm);
                    if obj.getQueuedCenterCount() > 0
                        obj.centerX_um = obj.queuedCenters_um(1,1);
                        obj.centerY_um = obj.queuedCenters_um(1,2);
                    end

                    if usedCachedImage
                        obj.setStatus(sprintf('Queued %d cell(s) from cached channel %d image. Queue size: %d.', ...
                            size(centersUm,1), chNum, obj.getQueuedCenterCount()));
                    else
                        obj.setStatus(sprintf('Queued %d cell(s) from channel %d image. Queue size: %d.', ...
                            size(centersUm,1), chNum, obj.getQueuedCenterCount()));
                    end
                    closePicker();
                catch
                    closePicker();
                end
            end

            function onCancel(~, ~)
                try
                    if isempty(hPickFig) || ~isvalid(hPickFig)
                        return;
                    end
                    obj.setStatus('Cell picker cancelled.');
                    closePicker();
                catch
                    closePicker();
                end
            end

            function closePicker()
                try
                    if ~isempty(hPickFig) && isvalid(hPickFig)
                        set(hPickFig, 'WindowButtonDownFcn', '', 'CloseRequestFcn', '');
                        delete(hPickFig);
                    end
                catch
                end
                if ~isempty(obj.hCellPickerFig) && isequal(obj.hCellPickerFig, hPickFig)
                    obj.hCellPickerFig = [];
                end
            end
        end

        function clearQueuedCenters(obj, varargin)
            obj.queuedCenters_um = zeros(0,2);
            obj.setStatus('Queued cells cleared.');
        end

        function runPsth(obj, varargin)
            try
                filePath = obj.lastSavePath;
                if isempty(filePath) || ~exist(filePath, 'file')
                    [f, p] = uigetfile('*.mat', 'Select stimData .mat file');
                    if isequal(f,0)
                        obj.setStatus('PSTH cancelled.');
                        return;
                    end
                    filePath = fullfile(p, f);
                end
                scanimage.guis.SlmSpiralPsthAnalysis(filePath);
                obj.plotInterpolatedTimeseries(filePath);
                obj.setStatus(sprintf('PSTH + timeseries loaded: %s', filePath));
            catch ME
                obj.setStatus(sprintf('PSTH failed: %s', ME.message));
            end
        end

        function plotInterpolatedTimeseries(~, filePath)
            data = load(filePath);
            assert(isfield(data, 'stimData') && ~isempty(data.stimData), ...
                'No stimData found in selected file.');
            stimData = data.stimData;

            traces = {};
            if isfield(stimData, 'traces') && ~isempty(stimData.traces)
                traces = stimData.traces;
            elseif isfield(stimData, 'trace') && ~isempty(stimData.trace)
                traces = {stimData.trace};
            end
            assert(~isempty(traces), 'Trace data missing in stimData.');

            [stimTimesAll, ~] = localResolveStimTriggerTimes(stimData);
            stimSpanAll = localResolveStimSpanFlags(stimData);
            perTrig = [];
            if isfield(stimData, 'stim_per_trigger') && ~isempty(stimData.stim_per_trigger)
                perTrig = stimData.stim_per_trigger(:);
            end

            nTraces = numel(traces);
            hFig = figure('Name','Single Cell Pulse Timeseries','Color','w');
            for iTrace = 1:nTraces
                tr = traces{iTrace};
                if ~isstruct(tr) || ~isfield(tr, 'times_s') || ~isfield(tr, 'values')
                    continue;
                end
                t = tr.times_s(:);
                y = tr.values(:);
                if numel(t) < 2 || numel(y) ~= numel(t)
                    continue;
                end

                [stimTimes, trigSelIdx] = localStimTimesForTrace(tr, iTrace, stimTimesAll, perTrig, stimData);
                stimSpan = true(numel(stimTimes),1);
                if ~isempty(stimSpanAll) && ~isempty(trigSelIdx)
                    nSel = min(numel(stimTimes), numel(trigSelIdx));
                    for iSel = 1:nSel
                        idxSel = trigSelIdx(iSel);
                        if isfinite(idxSel) && idxSel >= 1 && idxSel <= numel(stimSpanAll)
                            stimSpan(iSel) = logical(stimSpanAll(idxSel));
                        end
                    end
                end
                yInterp = localInterpolateStimFrames(t, y, stimTimes, stimSpan);

                ax = subplot(nTraces, 1, iTrace, 'Parent', hFig);
                hold(ax, 'on');
                plot(ax, t, y, 'Color', [0.7 0.7 0.7], 'LineWidth', 0.8);
                plot(ax, t, yInterp, 'b-', 'LineWidth', 1.2);

                if ~isempty(stimTimes)
                    stimTimes = stimTimes(isfinite(stimTimes));
                    stimTimes = stimTimes(stimTimes >= t(1) & stimTimes <= t(end));
                    if ~isempty(stimTimes)
                        yl = ylim(ax);
                        for k = 1:numel(stimTimes)
                            line(ax, [stimTimes(k) stimTimes(k)], yl, ...
                                'Color', [1 0 0], 'LineStyle', '--', 'LineWidth', 1);
                        end
                        ylim(ax, yl);
                    end
                end

                grid(ax, 'on');
                xlabel(ax, 'Time (s)');
                ylabel(ax, 'Ch1 Mean');
                title(ax, sprintf('Trace %d (gray=raw, blue=interpolated)', iTrace));
                hold(ax, 'off');
            end
        end

        function applyLoggingPreference(obj)
            if ~obj.forceEnableLoggingForStim
                return;
            end
            try
                hSI = obj.resolveSI();
                hPhotostim = hSI.hPhotostim;
                if most.idioms.isValidObj(hPhotostim)
                    obj.ensureLoggingForStim(hSI, hPhotostim);
                end
            catch
            end
        end

        function stopChannel1Logging(obj)
            obj.clearFrameTraceUpdates();
            for i = 1:numel(obj.hCh1Traces)
                hTrace = obj.hCh1Traces{i};
                if ~isempty(hTrace) && isvalid(hTrace)
                    try
                        hTrace.stop();
                    catch
                    end
                end
            end
            obj.hCh1Traces = {};
            obj.restoreIntegrationRoiConfig();
            obj.useIntegrationLogging = false;
            obj.traceFramesDoneStart = NaN;
            obj.traceFrameNumberStart = NaN;
            obj.traceFramesDoneEnd = NaN;
            obj.traceFrameNumberEnd = NaN;
        end

        function [idx, info] = createSingleStimGroup(obj)
            idx = [];
            info = struct('rep',1, 'combo_index',[1 1], 'linear_combo_index',1, ...
                'power_index',1, 'duration_index',1, 'center_index',1, ...
                'center_x_um',obj.centerX_um, 'center_y_um',obj.centerY_um, ...
                'group_index',1, 'power_pct',obj.power_pct, 'duration_ms',obj.duration_ms, ...
                'pulse_index',[]);

            hSI = obj.resolveSI();
            hPhotostim = hSI.hPhotostim;
            assert(most.idioms.isValidObj(hPhotostim), 'Photostim component is not available.');
            assert(~obj.isPhotostimActiveSafe(hPhotostim), 'Stop Photostim before editing stim groups.');
            assert(hPhotostim.numInstances > 0, 'Photostim is not configured.');
            assert(hPhotostim.hasSlm, 'No SLM available in the stimulation scannerset.');

            validateattributes(obj.spiralDiameter_um, {'numeric'}, {'scalar','positive','finite','nonnan'});
            validateattributes(obj.power_pct, {'numeric'}, {'scalar','finite','real','nonnan','>=',0,'<=',100});
            validateattributes(obj.duration_ms, {'numeric'}, {'scalar','positive','finite','real','nonnan'});
            center_um = obj.getCenter();

            assert(~isempty(hSI.objectiveResolution), 'objectiveResolution is not set in ScanImage.');
            sizeDeg = obj.spiralDiameter_um ./ hSI.objectiveResolution;
            centerDeg = (center_um + [obj.galvoOffsetX_um obj.galvoOffsetY_um]) ./ hSI.objectiveResolution;
            offsetDeg = [obj.galvoOffsetX_um obj.galvoOffsetY_um] ./ hSI.objectiveResolution;
            slmPattern = [-offsetDeg 0 1];

            nBeams = 1;
            try
                ss = hPhotostim.stimScannerset;
                if most.idioms.isValidObj(ss)
                    nBeams = numel(ss.beams);
                end
            catch
                nBeams = 1;
            end
            if nBeams < 3
                error('Photostim expects at least 3 beams; only %d configured.', nBeams);
            end

            hPhotostim.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1,0);
            hPhotostim.sequenceSelectedStimuli = [];

            sfStim = scanimage.mroi.scanfield.fields.StimulusField();
            sfStim.centerXY = centerDeg;
            sfStim.sizeXY = [sizeDeg sizeDeg];
            sfStim.duration = obj.duration_ms / 1000;
            sfStim.repetitions = 1;
            sfStim.stimfcnhdl = @scanimage.mroi.stimulusfunctions.logspiral;
            sfStim.stimparams = {'revolutions', obj.revolutions, 'direction', 'outward'};
            sfStim.slmPattern = slmPattern;
            powers = zeros(1,nBeams);
            powers(3) = obj.power_pct;
            sfStim.powers = powers;

            rois = scanimage.mroi.Roi.empty(1,0);
            if obj.prePause_ms > 0
                sfPause = scanimage.mroi.scanfield.fields.StimulusField();
                sfPause.centerXY = centerDeg;
                sfPause.sizeXY = [sizeDeg sizeDeg];
                sfPause.duration = obj.prePause_ms / 1000;
                sfPause.repetitions = 1;
                sfPause.stimfcnhdl = @scanimage.mroi.stimulusfunctions.pause;
                sfPause.stimparams = {'poweredPause', false};
                sfPause.powers = zeros(1,nBeams);
                roiPause = scanimage.mroi.Roi();
                roiPause.add(0, sfPause);
                rois(end+1) = roiPause; %#ok<AGROW>
            end

            roiStim = scanimage.mroi.Roi();
            roiStim.add(0, sfStim);
            rois(end+1) = roiStim; %#ok<AGROW>

            if obj.postPark_ms > 0
                sfPark = scanimage.mroi.scanfield.fields.StimulusField();
                sfPark.centerXY = centerDeg;
                sfPark.sizeXY = [sizeDeg sizeDeg];
                sfPark.duration = obj.postPark_ms / 1000;
                sfPark.repetitions = 1;
                sfPark.stimfcnhdl = @scanimage.mroi.stimulusfunctions.park;
                sfPark.stimparams = {};
                sfPark.powers = zeros(1,nBeams);
                roiPark = scanimage.mroi.Roi();
                roiPark.add(0, sfPark);
                rois(end+1) = roiPark; %#ok<AGROW>
            end

            rgName = sprintf('SLM Single (%.1f, %.1f) P%.1f D%.1f', ...
                center_um(1), center_um(2), obj.power_pct, obj.duration_ms);
            rg = scanimage.mroi.RoiGroup(rgName);
            for i = 1:numel(rois)
                rg.add(rois(i));
            end
            hPhotostim.stimRoiGroups = rg;
            hPhotostim.stimulusMode = 'sequence';
            hPhotostim.sequenceSelectedStimuli = 1;
            hPhotostim.numSequences = 1;

            idx = 1;
            info.center_x_um = center_um(1);
            info.center_y_um = center_um(2);
        end

        function [img, sf, chNum] = getPickerImageAndScanfield(~, hSI)
            img = [];
            sf = [];
            chNum = NaN;

            assert(most.idioms.isValidObj(hSI), 'ScanImage is not running.');
            assert(~isempty(hSI.hDisplay) && most.idioms.isValidObj(hSI.hDisplay), ...
                'Display component is not available.');
            assert(~isempty(hSI.hDisplay.rollingStripeDataBuffer) ...
                && ~isempty(hSI.hDisplay.rollingStripeDataBuffer{1}) ...
                && ~isempty(hSI.hDisplay.rollingStripeDataBuffer{1}{1}) ...
                && ~isempty(hSI.hDisplay.rollingStripeDataBuffer{1}{1}.roiData), ...
                'No rolling-average display image available. Start focus/acquisition first.');

            stripeAvg = hSI.hDisplay.rollingStripeDataBuffer{1}{1};
            rd = stripeAvg.roiData{1};
            assert(~isempty(rd.channels), 'No channel data available in rolling display buffer.');

            chIdxRaw = find(rd.channels == 2, 1, 'first');
            if isempty(chIdxRaw)
                error('Channel 2 is not available in current rolling display buffer.');
            end
            chNum = rd.channels(chIdxRaw);

            imgFromDisplay = [];
            try
                dispChans = hSI.hChannels.channelDisplay(:).';
                dispIdx = find(dispChans == 2, 1, 'first');
                if ~isempty(dispIdx)
                    avgFrames = hSI.hDisplay.lastAveragedFrame;
                    if iscell(avgFrames) && numel(avgFrames) >= dispIdx
                        imgFromDisplay = avgFrames{dispIdx};
                    end
                end
            catch
                imgFromDisplay = [];
            end

            if isempty(imgFromDisplay)
                img = rd.imageData{chIdxRaw};
                if iscell(img)
                    img = img{1};
                end
                try
                    img = single(img) ./ hSI.hDisplay.displayRollingAverageFactor;
                catch
                end
                if rd.transposed
                    img = img';
                end
            else
                img = imgFromDisplay;
            end
            assert(~isempty(img) && isnumeric(img), 'Selected channel 2 display image is empty.');

            try
                if isprop(rd, 'hRoi') && ~isempty(rd.hRoi)
                    if isprop(rd, 'zs') && ~isempty(rd.zs)
                        sf = rd.hRoi.get(rd.zs(1));
                    else
                        sf = rd.hRoi.scanfields(1);
                    end
                end
            catch
                sf = [];
            end
            if isempty(sf)
                rg = hSI.hRoiManager.currentRoiGroup;
                assert(~isempty(rg) && ~isempty(rg.rois), 'No imaging ROI group available.');
                sf = rg.rois(1).scanfields(1);
            end

            pixRes = sf.pixelResolutionXY;
            imgSizeXY = [size(img,2) size(img,1)];
            assert(isequal(imgSizeXY, pixRes), ...
                ['Displayed image size does not match scanfield resolution. ' ...
                 'Ensure you are picking on the active imaging plane and try again.']);
        end

        function [img, sf, chNum, usedCachedImage] = getPickerImageAndScanfieldForPicker(obj, hSI)
            [img, sf, chNum] = obj.getCachedCellPickerImageAndScanfield();
            usedCachedImage = ~isempty(img) && ~isempty(sf);
            if usedCachedImage
                return;
            end

            [img, sf, chNum] = obj.getPickerImageAndScanfield(hSI);
            obj.cacheCellPickerRedImage(img, sf, chNum);
        end

        function cacheCellPickerRedImage(obj, img, sf, chNum)
            if nargin < 4
                chNum = NaN;
            end
            obj.lastCellPickerRedImage = single(img);
            obj.lastCellPickerRedScanfield = sf;
            if isempty(chNum) || ~isfinite(chNum)
                obj.lastCellPickerRedChannel = NaN;
            else
                obj.lastCellPickerRedChannel = double(chNum);
            end
            obj.lastCellPickerRedTimestamp = datestr(now,'yyyy-mm-dd HH:MM:SS');
        end

        function [img, sf, chNum] = getCachedCellPickerImageAndScanfield(obj)
            img = [];
            sf = [];
            chNum = NaN;

            if isempty(obj.lastCellPickerRedImage)
                return;
            end

            img = obj.lastCellPickerRedImage;
            sf = obj.lastCellPickerRedScanfield;
            if isempty(sf)
                img = [];
                return;
            end

            try
                pixRes = sf.pixelResolutionXY;
                imgSizeXY = [size(img,2) size(img,1)];
                if ~isequal(imgSizeXY, pixRes)
                    img = [];
                    sf = [];
                    return;
                end
            catch
                img = [];
                sf = [];
                return;
            end

            if isfinite(obj.lastCellPickerRedChannel)
                chNum = obj.lastCellPickerRedChannel;
            else
                chNum = 2;
            end
        end

        function appendQueuedCenters(obj, centersUm)
            if isempty(centersUm)
                return;
            end
            validateattributes(centersUm, {'numeric'}, {'2d','ncols',2,'finite','real','nonnan'});
            obj.queuedCenters_um = [obj.queuedCenters_um; centersUm]; %#ok<AGROW>
        end

        function n = getQueuedCenterCount(obj)
            n = size(obj.queuedCenters_um, 1);
        end

        function [savePath, baseName, saveDir] = reserveSessionSavePath(obj)
            name = obj.animal_name;
            if isstring(name)
                name = char(name);
            end
            name = strtrim(name);
            if isempty(name)
                error('Animal Name is empty.');
            end

            baseName = obj.sanitizeFilename(name);
            if isempty(baseName)
                error('Animal Name produced an invalid filename.');
            end

            rootDir = pwd;
            stamp = datestr(now,'yyyymmdd_HHMMSS');
            folderBase = obj.sanitizeFilename(sprintf('%s_%s_data', baseName, stamp));
            if isempty(folderBase)
                error('Could not build valid session folder name.');
            end

            saveDir = fullfile(rootDir, folderBase);
            k = 1;
            while exist(saveDir, 'dir')
                saveDir = fullfile(rootDir, sprintf('%s_%02d', folderBase, k));
                k = k + 1;
                if k > 99
                    error('Could not reserve a unique session folder name.');
                end
            end

            [ok, msg] = mkdir(saveDir);
            if ~ok
                error('Could not create session folder: %s', msg);
            end

            savePath = fullfile(saveDir, [baseName '.mat']);
        end

        function savePath = saveSessionData(obj, stimTriggerTimes, idx, stimParamList, ...
                traceDataAllInput, traceLengthCheck, preferredSavePath, greenTiffPath, ...
                stimCommandTimes, stimTiming)
            savePath = '';
            try
                if nargin < 5 || isempty(traceDataAllInput)
                    traceDataAll = {};
                elseif iscell(traceDataAllInput)
                    traceDataAll = traceDataAllInput;
                else
                    traceDataAll = {traceDataAllInput};
                end
                if nargin < 6 || isempty(traceLengthCheck)
                    traceLengthCheck = struct();
                end
                if nargin < 7
                    preferredSavePath = '';
                end
                if nargin < 8
                    greenTiffPath = '';
                end
                if nargin < 9 || isempty(stimCommandTimes)
                    stimCommandTimes = stimTriggerTimes;
                end
                if nargin < 10 || ~isstruct(stimTiming) || isempty(stimTiming)
                    stimTiming = struct('source','software_markStim','hardware_available',false, ...
                        'hardware_aux_field','','hardware_event_count',0, ...
                        'expected_event_count',numel(stimTriggerTimes), ...
                        'hardware_tiff_path',greenTiffPath,'hardware_times_s',[], ...
                        'software_times_s',stimCommandTimes, ...
                        'hardware_frame_index',[],'hardware_line_index',[], ...
                        'hardware_end_frame_index',[],'hardware_end_line_index',[], ...
                        'hardware_spans_multiple_frames',[], ...
                        'hardware_active_duration_s',NaN, ...
                        'message','');
                end

                if ~isempty(preferredSavePath)
                    savePath = preferredSavePath;
                else
                    [savePath, ~, ~] = obj.reserveSessionSavePath();
                end

                params = struct();
                params.centerX_um = obj.centerX_um;
                params.centerY_um = obj.centerY_um;
                params.spiralDiameter_um = obj.spiralDiameter_um;
                params.power_pct = obj.power_pct;
                params.duration_ms = obj.duration_ms;
                params.pulse_count = obj.pulseCount;
                params.pulse_rate_hz = obj.pulseRate_Hz;
                params.prePause_ms = obj.prePause_ms;
                params.postPark_ms = obj.postPark_ms;
                params.preStimImaging_s = obj.preStimImaging_s;
                params.postStimImaging_s = obj.postStimImaging_s;
                params.revolutions = obj.revolutions;
                params.galvoOffsetX_um = obj.galvoOffsetX_um;
                params.galvoOffsetY_um = obj.galvoOffsetY_um;
                params.stimTriggerOnFrame = obj.stimTriggerOnFrame;
                params.forceEnableLoggingForStim = obj.forceEnableLoggingForStim;

                stimData = struct();
                stimData.animal_name = obj.animal_name;
                stimData.timestamp = datestr(now,'yyyy-mm-dd HH:MM:SS');
                [saveDir, ~, ~] = fileparts(savePath);
                stimData.session_dir = saveDir;
                stimData.params = params;
                stimData.power_values_pct = obj.power_pct;
                stimData.duration_values_ms = obj.duration_ms;
                stimData.stimulus_indices = idx;
                stimData.stim_trigger_times_s = stimTriggerTimes;
                stimData.stim_command_times_s = stimCommandTimes;
                if isfield(stimTiming,'source') && ~isempty(stimTiming.source)
                    stimData.stim_trigger_times_source = stimTiming.source;
                else
                    stimData.stim_trigger_times_source = 'software_markStim';
                end
                if isfield(stimTiming,'hardware_times_s') && ~isempty(stimTiming.hardware_times_s)
                    stimData.stim_trigger_times_hw_s = stimTiming.hardware_times_s(:);
                else
                    stimData.stim_trigger_times_hw_s = [];
                end
                if isfield(stimTiming,'hardware_frame_index') && ~isempty(stimTiming.hardware_frame_index)
                    stimData.stim_trigger_times_hw_frame_index = stimTiming.hardware_frame_index(:);
                else
                    stimData.stim_trigger_times_hw_frame_index = [];
                end
                if isfield(stimTiming,'hardware_line_index') && ~isempty(stimTiming.hardware_line_index)
                    stimData.stim_trigger_times_hw_line_index = stimTiming.hardware_line_index(:);
                else
                    stimData.stim_trigger_times_hw_line_index = [];
                end
                if isfield(stimTiming,'hardware_end_frame_index') && ~isempty(stimTiming.hardware_end_frame_index)
                    stimData.stim_trigger_times_hw_end_frame_index = stimTiming.hardware_end_frame_index(:);
                else
                    stimData.stim_trigger_times_hw_end_frame_index = [];
                end
                if isfield(stimTiming,'hardware_end_line_index') && ~isempty(stimTiming.hardware_end_line_index)
                    stimData.stim_trigger_times_hw_end_line_index = stimTiming.hardware_end_line_index(:);
                else
                    stimData.stim_trigger_times_hw_end_line_index = [];
                end
                if isfield(stimTiming,'hardware_spans_multiple_frames') && ~isempty(stimTiming.hardware_spans_multiple_frames)
                    stimData.stim_trigger_times_hw_spans_multiple_frames = logical(stimTiming.hardware_spans_multiple_frames(:));
                else
                    stimData.stim_trigger_times_hw_spans_multiple_frames = [];
                end
                stimData.stim_timing = stimTiming;
                stimData.stim_per_trigger = stimParamList;
                stimData.trace_length_check = traceLengthCheck;
                stimData.green_tiff_path = greenTiffPath;
                stimData.centers_um = obj.getCenter();
                picker = obj.getCellPickerRedSnapshot();
                stimData.cell_picker_red_image = picker.image;
                stimData.cell_picker_red_channel = picker.channel;
                stimData.cell_picker_red_timestamp = picker.timestamp;
                stimData.cell_picker_red_available = picker.available;
                if isempty(traceDataAll)
                    stimData.trace = [];
                    stimData.traces = {};
                else
                    stimData.traces = traceDataAll;
                    stimData.trace = traceDataAll{1};
                end

                save(savePath, 'stimData');
            catch ME
                obj.setStatus(sprintf('Save failed: %s', ME.message));
            end
        end

        function snap = getCellPickerRedSnapshot(obj)
            snap = struct('available',false, 'image',[], 'channel',NaN, 'timestamp','');
            try
                if ~isempty(obj.lastCellPickerRedImage)
                    snap.available = true;
                    snap.image = obj.lastCellPickerRedImage;
                end
                if isfinite(obj.lastCellPickerRedChannel)
                    snap.channel = obj.lastCellPickerRedChannel;
                else
                    snap.channel = NaN;
                end
                if ischar(obj.lastCellPickerRedTimestamp)
                    snap.timestamp = obj.lastCellPickerRedTimestamp;
                elseif isstring(obj.lastCellPickerRedTimestamp) && isscalar(obj.lastCellPickerRedTimestamp)
                    snap.timestamp = char(obj.lastCellPickerRedTimestamp);
                else
                    snap.timestamp = '';
                end
            catch
            end
        end

        function [stimTimesOut, timingInfo] = resolveStimTriggerTimesForSave(obj, stimTimesCommand, greenTiffPath, traceDataAll)
            if isstring(greenTiffPath)
                greenTiffPath = char(greenTiffPath);
            end
            stimTimesOut = stimTimesCommand(:);
            activeDuration_s = max(0, ...
                (localScalarNumeric(obj.prePause_ms,0) + localScalarNumeric(obj.duration_ms,0) + ...
                 localScalarNumeric(obj.postPark_ms,0)) / 1000);
            timingInfo = struct('source','software_markStim','hardware_available',false, ...
                'hardware_aux_field','','hardware_event_count',0, ...
                'expected_event_count',numel(stimTimesOut), ...
                'hardware_tiff_path',greenTiffPath,'hardware_times_s',[], ...
                'software_times_s',stimTimesOut, ...
                'hardware_frame_index',[],'hardware_line_index',[], ...
                'hardware_end_frame_index',[],'hardware_end_line_index',[], ...
                'hardware_spans_multiple_frames',[], ...
                'hardware_active_duration_s',activeDuration_s, ...
                'message','');

            if isempty(greenTiffPath) || ~ischar(greenTiffPath) || ~exist(greenTiffPath,'file')
                timingInfo.message = 'No green TIFF available for hardware trigger extraction.';
                return;
            end

            [stimTimesHw, hwInfo] = obj.extractHardwareStimTimesFromTiff( ...
                greenTiffPath, traceDataAll, numel(stimTimesOut), activeDuration_s);
            if isempty(stimTimesHw)
                if isfield(hwInfo,'message')
                    timingInfo.message = hwInfo.message;
                end
                return;
            end

            timingInfo.hardware_available = true;
            timingInfo.source = 'hardware_aux_trigger';
            if isfield(hwInfo,'aux_field')
                timingInfo.hardware_aux_field = hwInfo.aux_field;
            end
            timingInfo.hardware_event_count = numel(stimTimesHw);
            timingInfo.hardware_times_s = stimTimesHw(:);
            hwFrameIdx = [];
            hwLineIdx = [];
            hwEndFrameIdx = [];
            hwEndLineIdx = [];
            hwSpans = [];
            if isfield(hwInfo,'hardware_frame_index') && ~isempty(hwInfo.hardware_frame_index)
                hwFrameIdx = hwInfo.hardware_frame_index(:);
            end
            if isfield(hwInfo,'hardware_line_index') && ~isempty(hwInfo.hardware_line_index)
                hwLineIdx = hwInfo.hardware_line_index(:);
            end
            if isfield(hwInfo,'hardware_end_frame_index') && ~isempty(hwInfo.hardware_end_frame_index)
                hwEndFrameIdx = hwInfo.hardware_end_frame_index(:);
            end
            if isfield(hwInfo,'hardware_end_line_index') && ~isempty(hwInfo.hardware_end_line_index)
                hwEndLineIdx = hwInfo.hardware_end_line_index(:);
            end
            if isfield(hwInfo,'hardware_spans_multiple_frames') && ~isempty(hwInfo.hardware_spans_multiple_frames)
                hwSpans = logical(hwInfo.hardware_spans_multiple_frames(:));
            end
            if isfield(hwInfo,'message')
                timingInfo.message = hwInfo.message;
            end

            nExpected = numel(stimTimesOut);
            keepIdx = (1:numel(stimTimesHw)).';
            if nExpected > 0 && numel(stimTimesHw) > nExpected
                if ~isempty(stimTimesOut)
                    % Keep monotonic hardware events nearest each software command time.
                    chosen = nan(nExpected,1);
                    hw = stimTimesHw(:);
                    startIdx = 1;
                    for iEvt = 1:nExpected
                        if startIdx > numel(hw)
                            break;
                        end
                        [~, relIdx] = min(abs(hw(startIdx:end) - stimTimesOut(iEvt)));
                        absIdx = startIdx + relIdx - 1;
                        chosen(iEvt) = absIdx;
                        startIdx = absIdx + 1;
                    end
                    chosen = chosen(isfinite(chosen));
                    if numel(chosen) == nExpected
                        keepIdx = chosen;
                    else
                        keepIdx = (1:nExpected).';
                    end
                else
                    keepIdx = (1:nExpected).';
                end
            elseif nExpected > 0 && numel(stimTimesHw) < nExpected
                timingInfo.message = sprintf('Hardware trigger count mismatch: expected %d, found %d.', ...
                    nExpected, numel(stimTimesHw));
            end

            keepIdx = keepIdx(keepIdx >= 1 & keepIdx <= numel(stimTimesHw));
            stimTimesHw = stimTimesHw(keepIdx);
            if ~isempty(hwFrameIdx) && ~isempty(keepIdx) && numel(hwFrameIdx) >= max(keepIdx)
                hwFrameIdx = hwFrameIdx(keepIdx);
            else
                hwFrameIdx = [];
            end
            if ~isempty(hwLineIdx) && ~isempty(keepIdx) && numel(hwLineIdx) >= max(keepIdx)
                hwLineIdx = hwLineIdx(keepIdx);
            else
                hwLineIdx = [];
            end
            if ~isempty(hwEndFrameIdx) && ~isempty(keepIdx) && numel(hwEndFrameIdx) >= max(keepIdx)
                hwEndFrameIdx = hwEndFrameIdx(keepIdx);
            else
                hwEndFrameIdx = [];
            end
            if ~isempty(hwEndLineIdx) && ~isempty(keepIdx) && numel(hwEndLineIdx) >= max(keepIdx)
                hwEndLineIdx = hwEndLineIdx(keepIdx);
            else
                hwEndLineIdx = [];
            end
            if ~isempty(hwSpans) && ~isempty(keepIdx) && numel(hwSpans) >= max(keepIdx)
                hwSpans = hwSpans(keepIdx);
            else
                hwSpans = [];
            end

            stimTimesOut = stimTimesHw(:);
            timingInfo.hardware_event_count = numel(stimTimesOut);
            timingInfo.hardware_times_s = stimTimesOut;
            timingInfo.hardware_frame_index = hwFrameIdx(:);
            timingInfo.hardware_line_index = hwLineIdx(:);
            timingInfo.hardware_end_frame_index = hwEndFrameIdx(:);
            timingInfo.hardware_end_line_index = hwEndLineIdx(:);
            timingInfo.hardware_spans_multiple_frames = logical(hwSpans(:));
        end

        function [stimTimesTrace_s, info] = extractHardwareStimTimesFromTiff(obj, tiffPath, traceDataAll, nExpected, activeDuration_s)
            if nargin < 4 || isempty(nExpected) || ~isfinite(nExpected)
                nExpected = NaN;
            end
            if nargin < 5 || isempty(activeDuration_s) || ~isfinite(activeDuration_s)
                activeDuration_s = NaN;
            end

            stimTimesTrace_s = [];
            info = struct('aux_field','','message','','hardware_times_raw',[], ...
                'hardware_times_frame_s',[], ...
                'hardware_end_times_frame_s',[], ...
                'hardware_end_times_trace_s',[], ...
                'hardware_frame_index',[],'hardware_line_index',[], ...
                'hardware_end_frame_index',[],'hardware_end_line_index',[], ...
                'hardware_spans_multiple_frames',[], ...
                'frame_timestamps_s',[],'lines_per_frame',NaN,'frame_rate_hz',NaN, ...
                'active_duration_s',activeDuration_s);

            try
                hdr = scanimage.util.opentif(tiffPath);
            catch ME
                info.message = sprintf('Failed to read TIFF header: %s', ME.message);
                return;
            end

            if ~isstruct(hdr) || isempty(hdr)
                info.message = 'TIFF header is empty.';
                return;
            end

            frameTs = [];
            if isfield(hdr,'frameTimestamps_sec') && ~isempty(hdr.frameTimestamps_sec)
                frameTs = double(hdr.frameTimestamps_sec(:));
            elseif isfield(hdr,'frameTimestamp') && ~isempty(hdr.frameTimestamp)
                frameTs = double(hdr.frameTimestamp(:));
            end
            frameTs = frameTs(isfinite(frameTs));
            info.frame_timestamps_s = frameTs;

            linesPerFrame = NaN;
            frameRateHz = NaN;
            try
                if isfield(hdr,'SI') && isstruct(hdr.SI) && isfield(hdr.SI,'hRoiManager')
                    linesPerFrame = localScalarNumeric(hdr.SI.hRoiManager.linesPerFrame, NaN);
                    frameRateHz = localScalarNumeric(hdr.SI.hRoiManager.scanFrameRate, NaN);
                elseif isfield(hdr,'scanimage') && isstruct(hdr.scanimage) ...
                        && isfield(hdr.scanimage,'SI') && isstruct(hdr.scanimage.SI) ...
                        && isfield(hdr.scanimage.SI,'hRoiManager')
                    linesPerFrame = localScalarNumeric(hdr.scanimage.SI.hRoiManager.linesPerFrame, NaN);
                    frameRateHz = localScalarNumeric(hdr.scanimage.SI.hRoiManager.scanFrameRate, NaN);
                end
            catch
                linesPerFrame = NaN;
                frameRateHz = NaN;
            end
            if ~isfinite(frameRateHz) && numel(frameTs) >= 2
                dFrame = diff(frameTs);
                dFrame = dFrame(isfinite(dFrame) & dFrame > 0);
                if ~isempty(dFrame)
                    frameRateHz = 1 / median(dFrame);
                end
            end
            info.lines_per_frame = linesPerFrame;
            info.frame_rate_hz = frameRateHz;

            auxFields = {'auxTrigger2','auxTrigger3','auxTrigger1','auxTrigger0'};
            bestField = '';
            bestTimes = [];
            bestScore = -inf;

            for iF = 1:numel(auxFields)
                fn = auxFields{iF};
                if ~isfield(hdr, fn) || isempty(hdr.(fn))
                    continue;
                end

                vals = obj.flattenAuxTriggerValues(hdr.(fn));
                if isempty(vals)
                    continue;
                end
                vals = unique(vals(:), 'stable');

                score = numel(vals);
                if isfinite(nExpected)
                    score = score - abs(numel(vals) - nExpected);
                end
                if strcmp(fn, 'auxTrigger2')
                    score = score + 0.25; % Prefer imaging Aux Trigger 3 (0-based index 2).
                end

                if score > bestScore
                    bestScore = score;
                    bestField = fn;
                    bestTimes = vals;
                end
            end

            if isempty(bestTimes)
                info.message = 'No aux trigger events found in TIFF header.';
                return;
            end

            hwTimes = double(bestTimes(:));
            info.hardware_times_raw = hwTimes;
            info.aux_field = bestField;

            if ~isempty(frameTs)
                tMin = min(frameTs);
                tMax = max(frameTs);
                if all(hwTimes >= (tMin - 1) & hwTimes <= (tMax + 1))
                    % already in frame timestamp reference
                elseif all(hwTimes >= -1) && all(hwTimes <= ((tMax - tMin) + 1))
                    hwTimes = hwTimes + tMin;
                end
            end
            info.hardware_times_frame_s = hwTimes;
            [hwFrameIdx, hwLineIdx] = obj.mapStimTimesToFrameLine(hwTimes, frameTs, linesPerFrame, frameRateHz);
            info.hardware_frame_index = hwFrameIdx;
            info.hardware_line_index = hwLineIdx;
            hwEndTimes = [];
            if isfinite(activeDuration_s) && activeDuration_s > 0
                hwEndTimes = hwTimes + activeDuration_s;
                info.hardware_end_times_frame_s = hwEndTimes;
                [hwEndFrameIdx, hwEndLineIdx] = obj.mapStimTimesToFrameLine(hwEndTimes, frameTs, linesPerFrame, frameRateHz);
                info.hardware_end_frame_index = hwEndFrameIdx;
                info.hardware_end_line_index = hwEndLineIdx;
                if numel(hwEndFrameIdx) == numel(hwFrameIdx)
                    info.hardware_spans_multiple_frames = hwEndFrameIdx > hwFrameIdx;
                end
            end

            traceStart_s = NaN;
            if iscell(traceDataAll)
                for iTr = 1:numel(traceDataAll)
                    tr = traceDataAll{iTr};
                    if ~isstruct(tr) || ~isfield(tr,'times_s') || isempty(tr.times_s)
                        continue;
                    end
                    t = tr.times_s(:);
                    t = t(isfinite(t));
                    if ~isempty(t)
                        traceStart_s = t(1);
                        break;
                    end
                end
            end

            if isfinite(traceStart_s) && ~isempty(frameTs)
                frameStart_s = frameTs(1);
                stimTimesTrace_s = hwTimes - frameStart_s + traceStart_s;
                if ~isempty(hwEndTimes)
                    info.hardware_end_times_trace_s = hwEndTimes - frameStart_s + traceStart_s;
                end
            else
                stimTimesTrace_s = hwTimes;
                if ~isempty(hwEndTimes)
                    info.hardware_end_times_trace_s = hwEndTimes;
                end
            end

            validMask = isfinite(stimTimesTrace_s);
            stimTimesTrace_s = stimTimesTrace_s(validMask);
            if numel(info.hardware_frame_index) == numel(validMask)
                info.hardware_frame_index = info.hardware_frame_index(validMask);
            else
                info.hardware_frame_index = [];
            end
            if numel(info.hardware_line_index) == numel(validMask)
                info.hardware_line_index = info.hardware_line_index(validMask);
            else
                info.hardware_line_index = [];
            end
            if numel(info.hardware_end_frame_index) == numel(validMask)
                info.hardware_end_frame_index = info.hardware_end_frame_index(validMask);
            else
                info.hardware_end_frame_index = [];
            end
            if numel(info.hardware_end_line_index) == numel(validMask)
                info.hardware_end_line_index = info.hardware_end_line_index(validMask);
            else
                info.hardware_end_line_index = [];
            end
            if numel(info.hardware_spans_multiple_frames) == numel(validMask)
                info.hardware_spans_multiple_frames = info.hardware_spans_multiple_frames(validMask);
            else
                info.hardware_spans_multiple_frames = [];
            end
            if numel(info.hardware_end_times_trace_s) == numel(validMask)
                info.hardware_end_times_trace_s = info.hardware_end_times_trace_s(validMask);
            else
                info.hardware_end_times_trace_s = [];
            end
            stimTimesTrace_s = stimTimesTrace_s(:);

            if isempty(stimTimesTrace_s)
                info.message = sprintf('No valid hardware trigger events extracted from %s.', tiffPath);
            else
                info.message = sprintf('Using %d hardware trigger event(s) from %s (%s).', ...
                    numel(stimTimesTrace_s), info.aux_field, tiffPath);
            end
        end

        function vals = flattenAuxTriggerValues(~, v)
            vals = [];
            if isempty(v)
                return;
            end

            stack = {v};
            while ~isempty(stack)
                cur = stack{end};
                stack(end) = [];
                if isempty(cur)
                    continue;
                end

                if isnumeric(cur) || islogical(cur)
                    vals = [vals; double(cur(:))]; %#ok<AGROW>
                    continue;
                end

                if iscell(cur)
                    for i = numel(cur):-1:1
                        stack{end+1} = cur{i}; %#ok<AGROW>
                    end
                    continue;
                end

                if isstruct(cur)
                    fn = fieldnames(cur);
                    for i = 1:numel(cur)
                        for j = 1:numel(fn)
                            try
                                stack{end+1} = cur(i).(fn{j}); %#ok<AGROW>
                            catch
                            end
                        end
                    end
                end
            end

            vals = vals(isfinite(vals));
        end

        function [frameIdx, lineIdx] = mapStimTimesToFrameLine(~, stimTimes, frameTs, linesPerFrame, frameRateHz)
            stimTimes = stimTimes(:);
            frameIdx = nan(numel(stimTimes),1);
            lineIdx = nan(numel(stimTimes),1);
            if isempty(stimTimes) || isempty(frameTs)
                return;
            end

            frameTs = frameTs(:);
            frameTs = frameTs(isfinite(frameTs));
            if isempty(frameTs)
                return;
            end

            if ~isfinite(linesPerFrame) || linesPerFrame <= 0
                return;
            end
            linesPerFrame = max(1, round(linesPerFrame));

            if ~isfinite(frameRateHz) || frameRateHz <= 0
                dFrame = diff(frameTs);
                dFrame = dFrame(isfinite(dFrame) & dFrame > 0);
                if ~isempty(dFrame)
                    frameRateHz = 1 / median(dFrame);
                end
            end

            for iEvt = 1:numel(stimTimes)
                tEvt = stimTimes(iEvt);
                if ~isfinite(tEvt)
                    continue;
                end

                idx = find(frameTs <= tEvt, 1, 'last');
                if isempty(idx)
                    idx = 1;
                end
                frameIdx(iEvt) = idx;

                frameDur = NaN;
                if idx < numel(frameTs)
                    frameDur = frameTs(idx+1) - frameTs(idx);
                end
                if ~(isfinite(frameDur) && frameDur > 0) && isfinite(frameRateHz) && frameRateHz > 0
                    frameDur = 1 / frameRateHz;
                end
                if ~(isfinite(frameDur) && frameDur > 0)
                    continue;
                end

                rel = (tEvt - frameTs(idx)) / frameDur;
                line = floor(rel * linesPerFrame) + 1;
                line = max(1, min(linesPerFrame, line));
                lineIdx(iEvt) = line;
            end
        end

        function run_s = estimateRunDuration_s(obj, hSI)
            validateattributes(obj.pulseCount, {'numeric'}, {'scalar','finite','real','nonnan','>=',1});
            validateattributes(obj.pulseRate_Hz, {'numeric'}, {'scalar','finite','real','nonnan','>',0});
            pulseTrain_s = (max(1, round(obj.pulseCount)) - 1) / max(eps, obj.pulseRate_Hz);
            stimTail_s = (obj.prePause_ms + obj.duration_ms + obj.postPark_ms) / 1000;
            preStim_s = max(0, localScalarNumeric(obj.preStimImaging_s, 30));
            postStim_s = max(0, localScalarNumeric(obj.postStimImaging_s, 30));
            run_s = preStim_s + pulseTrain_s + stimTail_s + postStim_s;
            if obj.stimTriggerOnFrame
                run_s = run_s + (max(1, round(obj.pulseCount)) / obj.getFrameRateSafe(hSI));
            end
            run_s = run_s + obj.grabFramePadding_s;
        end

        function rate = getFrameRateSafe(~, hSI)
            rate = NaN;
            try
                if most.idioms.isValidObj(hSI) && ~isempty(hSI.hRoiManager) && most.idioms.isValidObj(hSI.hRoiManager)
                    rate = localScalarNumeric(hSI.hRoiManager.scanFrameRate, NaN);
                end
            catch
                rate = NaN;
            end
            if ~isfinite(rate) || rate <= 0
                rate = 30;
            end
        end

        function cfg = emptyGrabRunConfig(~)
            cfg = struct();
            cfg.valid = false;
            cfg.startedAcq = false;
            cfg.tStartDatenum = NaN;
            cfg.logFileStem = '';
            cfg.logFilePath = '';
            cfg.greenChannel = NaN;
            cfg.oldChannelSave = [];
            cfg.oldLogFileStem = '';
            cfg.oldLogFilePath = '';
            cfg.oldFramesPerAcq = [];
            cfg.oldLogFramesPerFile = [];
            cfg.oldStackEnable = [];
            cfg.oldStackFramesPerSlice = [];
            cfg.usedStackFramesFallback = false;
        end

        function cfg = configureGrabForGreenTiff(obj, hSI, logStem, logPath, run_s)
            cfg = obj.emptyGrabRunConfig();
            cfg.logFileStem = logStem;
            cfg.logFilePath = logPath;
            cfg.tStartDatenum = now;

            assert(most.idioms.isValidObj(hSI) && ~isempty(hSI.hScan2D) && most.idioms.isValidObj(hSI.hScan2D), ...
                'Scan2D component is not available.');
            assert(most.idioms.isValidObj(hSI.hChannels), 'Channels component is not available.');
            assert(~obj.isSiActiveSafe(hSI), 'Cannot configure GRAB logging while acquisition is already active.');

            hScan2D = hSI.hScan2D;
            hChannels = hSI.hChannels;
            cfg.oldChannelSave = hChannels.channelSave;
            cfg.oldLogFileStem = hScan2D.logFileStem;
            cfg.oldLogFilePath = hScan2D.logFilePath;
            cfg.oldFramesPerAcq = hScan2D.framesPerAcq;
            cfg.oldLogFramesPerFile = hScan2D.logFramesPerFile;
            cfg.greenChannel = obj.resolveGreenChannel(hSI);
            try
                if ~isempty(hSI.hStackManager) && most.idioms.isValidObj(hSI.hStackManager)
                    cfg.oldStackEnable = hSI.hStackManager.enable;
                    cfg.oldStackFramesPerSlice = hSI.hStackManager.framesPerSlice;
                end
            catch
            end

            try
                if ~hChannels.loggingEnable
                    hChannels.loggingEnable = true;
                end
                hChannels.channelSave = cfg.greenChannel;
                hScan2D.logFilePath = logPath;
                hScan2D.logFileStem = logStem;
                hScan2D.logFramesPerFile = inf;
                nFrames = max(1, ceil(run_s * obj.getFrameRateSafe(hSI)));
                try
                    hScan2D.framesPerAcq = nFrames;
                catch MEsetFrames
                    % Some scan backends expose framesPerAcq as read-only to GUI code.
                    if ~isempty(hSI.hStackManager) && most.idioms.isValidObj(hSI.hStackManager)
                        cfg.usedStackFramesFallback = true;
                        hSI.hStackManager.enable = false;
                        hSI.hStackManager.framesPerSlice = nFrames;
                    else
                        rethrow(MEsetFrames);
                    end
                end
                hSI.startGrab();
                cfg.startedAcq = true;
                cfg.valid = true;
            catch ME
                obj.restoreGrabRunConfig(hSI, cfg);
                error('Failed to configure/start GRAB logging: %s', ME.message);
            end
        end

        function restoreGrabRunConfig(~, hSI, cfg)
            if ~isstruct(cfg)
                return;
            end
            if ~most.idioms.isValidObj(hSI) || isempty(hSI.hScan2D) || ~most.idioms.isValidObj(hSI.hScan2D)
                return;
            end
            if ~isempty(hSI.active) && hSI.active
                return;
            end

            try
                if isfield(cfg,'oldChannelSave') && ~isempty(cfg.oldChannelSave) && most.idioms.isValidObj(hSI.hChannels)
                    hSI.hChannels.channelSave = cfg.oldChannelSave;
                end
            catch
            end
            try
                if isfield(cfg,'oldLogFileStem') && ~isempty(cfg.oldLogFileStem)
                    hSI.hScan2D.logFileStem = cfg.oldLogFileStem;
                end
            catch
            end
            try
                if isfield(cfg,'oldLogFilePath') && ~isempty(cfg.oldLogFilePath)
                    hSI.hScan2D.logFilePath = cfg.oldLogFilePath;
                end
            catch
            end
            try
                if isfield(cfg,'oldFramesPerAcq') && ~isempty(cfg.oldFramesPerAcq)
                    hSI.hScan2D.framesPerAcq = cfg.oldFramesPerAcq;
                end
            catch
            end
            try
                if isfield(cfg,'oldLogFramesPerFile') && ~isempty(cfg.oldLogFramesPerFile)
                    hSI.hScan2D.logFramesPerFile = cfg.oldLogFramesPerFile;
                end
            catch
            end
            try
                if ~isempty(hSI.hStackManager) && most.idioms.isValidObj(hSI.hStackManager)
                    if isfield(cfg,'oldStackFramesPerSlice') && ~isempty(cfg.oldStackFramesPerSlice)
                        hSI.hStackManager.framesPerSlice = cfg.oldStackFramesPerSlice;
                    end
                    if isfield(cfg,'oldStackEnable') && ~isempty(cfg.oldStackEnable)
                        hSI.hStackManager.enable = logical(cfg.oldStackEnable);
                    end
                end
            catch
            end
        end

        function outPath = finalizeGreenTiff(~, cfg, matPath)
            outPath = '';
            if ~isstruct(cfg) || ~isfield(cfg,'valid') || ~cfg.valid
                return;
            end
            if isempty(matPath) || ~ischar(matPath)
                return;
            end

            [matDir, matBase] = fileparts(matPath);
            pattern = fullfile(cfg.logFilePath, sprintf('%s*.tif', cfg.logFileStem));

            t0 = tic;
            srcPath = '';
            while toc(t0) < 4
                f = dir(pattern);
                if ~isempty(f) && isfield(cfg,'tStartDatenum') && isfinite(cfg.tStartDatenum)
                    f = f([f.datenum] >= (cfg.tStartDatenum - 2/86400));
                end
                if ~isempty(f)
                    if isfield(cfg,'greenChannel') && isfinite(cfg.greenChannel)
                        tag = sprintf('_chn%d', round(cfg.greenChannel));
                        tagged = contains({f.name}, tag);
                        if any(tagged)
                            f = f(tagged);
                        end
                    end
                    [~, k] = max([f.datenum]);
                    srcPath = fullfile(f(k).folder, f(k).name);
                    break;
                end
                pause(0.1);
            end

            if isempty(srcPath) || ~exist(srcPath,'file')
                return;
            end

            targetPath = fullfile(matDir, [matBase '.tif']);
            if strcmpi(srcPath, targetPath)
                outPath = srcPath;
                return;
            end
            if exist(targetPath,'file')
                targetPath = fullfile(matDir, [matBase '_green.tif']);
            end

            try
                movefile(srcPath, targetPath);
                outPath = targetPath;
            catch
                outPath = srcPath;
            end
        end

        function ch = resolveGreenChannel(~, hSI)
            ch = [];
            try
                mergeColors = hSI.hChannels.channelMergeColor;
                if ischar(mergeColors)
                    mergeColors = {mergeColors};
                end
                idx = find(strcmpi(mergeColors,'green'), 1, 'first');
                if ~isempty(idx)
                    ch = idx;
                end
            catch
                ch = [];
            end
            if isempty(ch)
                try
                    if ~isempty(hSI.hChannels.channelSave)
                        ch = hSI.hChannels.channelSave(1);
                    end
                catch
                    ch = [];
                end
            end
            if isempty(ch)
                ch = 1;
            end
            ch = round(ch(1));
        end

        function startChannel1Logging(obj, hSI, centers)
            obj.stopChannel1Logging();
            if isempty(centers)
                return;
            end

            obj.traceFramesDoneStart = obj.getFramesDoneSafe(hSI);
            obj.traceFrameNumberStart = obj.getFrameNumberSafe(hSI);
            obj.traceFramesDoneEnd = NaN;
            obj.traceFrameNumberEnd = NaN;

            obj.useIntegrationLogging = obj.configureIntegrationRoisForLogging(hSI, centers);
            nCenters = size(centers,1);
            obj.hCh1Traces = cell(nCenters,1);
            for i = 1:nCenters
                hTrace = scanimage.guis.SlmSpiralPatternChannel1Trace(hSI, centers(i,:), 10, [], i, false);
                if obj.useIntegrationLogging
                    hTrace.start(false, false);
                else
                    hTrace.start(true, true);
                end
                obj.hCh1Traces{i} = hTrace;
            end

            if obj.useIntegrationLogging
                obj.startFrameTraceUpdates(hSI);
            end
        end

        function traceDataAll = collectTraceData(obj)
            traceDataAll = {};
            for i = 1:numel(obj.hCh1Traces)
                hTrace = obj.hCh1Traces{i};
                if ~isempty(hTrace) && isvalid(hTrace)
                    try
                        traceDataAll{end+1,1} = hTrace.getData(); %#ok<AGROW>
                    catch
                    end
                end
            end
        end

        function tf = configureIntegrationRoisForLogging(obj, hSI, centers)
            tf = false;
            obj.integrationConfigBackup = struct('valid',false);
            if isempty(centers) || ~most.idioms.isValidObj(hSI)
                return;
            end

            try
                hInt = hSI.hIntegrationRoiManager;
                if ~most.idioms.isValidObj(hInt)
                    return;
                end
            catch
                return;
            end

            try
                if obj.isSiActiveSafe(hSI) && ~hInt.enable
                    return;
                end
            catch
            end

            try
                backup = struct();
                backup.valid = true;
                backup.enable = hInt.enable;
                backup.enableDisplay = hInt.enableDisplay;
                backup.roiGroup = [];
                try
                    if ~isempty(hInt.roiGroup) && most.idioms.isValidObj(hInt.roiGroup)
                        backup.roiGroup = hInt.roiGroup.copy();
                    end
                catch
                    backup.roiGroup = [];
                end
                obj.integrationConfigBackup = backup;

                rg = obj.buildIntegrationRoiGroup(hSI, centers);
                if isempty(rg) || isempty(rg.rois)
                    obj.integrationConfigBackup = struct('valid',false);
                    return;
                end

                hInt.roiGroup = rg;
                try
                    hInt.enableDisplay = false;
                catch
                end
                hInt.enable = true;
                tf = logical(hInt.enable) && numel(hInt.roiGroup.rois) == size(centers,1);
            catch
                obj.integrationConfigBackup = struct('valid',false);
                tf = false;
            end
        end

        function restoreIntegrationRoiConfig(obj)
            backup = obj.integrationConfigBackup;
            obj.integrationConfigBackup = struct('valid',false);
            if ~isstruct(backup) || ~isfield(backup,'valid') || ~backup.valid
                return;
            end

            hSI = [];
            try
                hSI = obj.resolveSI();
            catch
            end
            if ~most.idioms.isValidObj(hSI)
                return;
            end

            try
                hInt = hSI.hIntegrationRoiManager;
                if ~most.idioms.isValidObj(hInt)
                    return;
                end
                if isfield(backup,'roiGroup') && ~isempty(backup.roiGroup) && most.idioms.isValidObj(backup.roiGroup)
                    hInt.roiGroup = backup.roiGroup;
                else
                    hInt.roiGroup = scanimage.mroi.RoiGroup();
                end
                if isfield(backup,'enableDisplay') && ~isempty(backup.enableDisplay)
                    hInt.enableDisplay = logical(backup.enableDisplay);
                end
                if isfield(backup,'enable') && ~isempty(backup.enable)
                    hInt.enable = logical(backup.enable);
                end
            catch
            end
        end

        function rg = buildIntegrationRoiGroup(~, hSI, centers)
            rg = scanimage.mroi.RoiGroup('SlmSingleCellPulse Integration');
            if isempty(centers)
                return;
            end

            assert(~isempty(hSI.objectiveResolution), 'objectiveResolution is not set in ScanImage.');
            res = hSI.objectiveResolution;
            if isscalar(res)
                resXY = [res res];
            else
                assert(numel(res) >= 2, 'objectiveResolution must be scalar or 2-element.');
                resXY = res(1:2);
            end
            validateattributes(resXY,{'numeric'},{'vector','numel',2,'finite','real','nonnan','positive'});

            zList = 0;
            try
                zs = hSI.hStackManager.zs;
                if ~isempty(zs)
                    zList = unique(zs(:).', 'stable');
                end
            catch
            end
            if isempty(zList)
                zList = 0;
            end

            roiDiameterUm = 10;
            roiSizeRef = roiDiameterUm ./ resXY;
            [xx,yy] = meshgrid(linspace(-1,1,17), linspace(-1,1,17));
            roiMask = double((xx.^2 + yy.^2) <= 1);

            for i = 1:size(centers,1)
                centerRef = centers(i,:) ./ resXY;
                roi = scanimage.mroi.Roi();
                roi.name = sprintf('SinglePulseTrace C%d', i);
                for iz = 1:numel(zList)
                    sf = scanimage.mroi.scanfield.fields.IntegrationField();
                    sf.centerXY = centerRef;
                    sf.sizeXY = roiSizeRef;
                    sf.rotationDegrees = 0;
                    sf.channel = 1;
                    sf.processor = 'cpu';
                    sf.mask = roiMask;
                    roi.add(zList(iz), sf);
                end
                rg.add(roi);
            end
        end

        function startFrameTraceUpdates(obj, hSI)
            obj.clearFrameTraceUpdates();
            if isempty(obj.hCh1Traces)
                return;
            end

            listenerOk = false;
            try
                if most.idioms.isValidObj(hSI) && ~isempty(hSI.hUserFunctions) && most.idioms.isValidObj(hSI.hUserFunctions)
                    obj.hFrameTraceListener = addlistener(hSI.hUserFunctions, ...
                        'frameAcquired', @(~,~)obj.processIntegrationFrames(hSI));
                    listenerOk = true;
                end
            catch
                obj.hFrameTraceListener = [];
            end

            if ~listenerOk
                obj.hFrameTraceTimer = timer( ...
                    'Name','SlmSingleCellPulseFrameTrace', ...
                    'ExecutionMode','fixedSpacing', ...
                    'Period',0.05, ...
                    'TimerFcn',@(~,~)obj.processIntegrationFrames(hSI));
                start(obj.hFrameTraceTimer);
            end
        end

        function clearFrameTraceUpdates(obj)
            try
                if ~isempty(obj.hFrameTraceListener) && isvalid(obj.hFrameTraceListener)
                    delete(obj.hFrameTraceListener);
                end
            catch
            end
            obj.hFrameTraceListener = [];

            try
                if ~isempty(obj.hFrameTraceTimer) && isvalid(obj.hFrameTraceTimer)
                    stop(obj.hFrameTraceTimer);
                    delete(obj.hFrameTraceTimer);
                end
            catch
            end
            obj.hFrameTraceTimer = [];
        end

        function processIntegrationFrames(obj, hSI)
            if ~obj.useIntegrationLogging || isempty(obj.hCh1Traces) || ~most.idioms.isValidObj(hSI)
                return;
            end
            try
                hInt = hSI.hIntegrationRoiManager;
                if ~most.idioms.isValidObj(hInt)
                    return;
                end
                [~, values, ~, frameNumbers] = hInt.getIntegrationValues();
                n = min(numel(values), numel(obj.hCh1Traces));
                for i = 1:n
                    hTrace = obj.hCh1Traces{i};
                    if isempty(hTrace) || ~isvalid(hTrace)
                        continue;
                    end
                    if numel(frameNumbers) < i || ~isfinite(frameNumbers(i)) || frameNumbers(i) <= 0
                        continue;
                    end
                    hTrace.appendSample(frameNumbers(i), [], values(i));
                end
            catch
            end
        end

        function check = buildTraceLengthCheck(obj, traceDataAll)
            check = struct();
            frameNumStart = localScalarNumeric(obj.traceFrameNumberStart, NaN);
            frameNumEnd = localScalarNumeric(obj.traceFrameNumberEnd, NaN);
            framesDoneStart = localScalarNumeric(obj.traceFramesDoneStart, NaN);
            framesDoneEnd = localScalarNumeric(obj.traceFramesDoneEnd, NaN);
            check.framesDoneStart = framesDoneStart;
            check.framesDoneEnd = framesDoneEnd;
            check.frameNumberStart = frameNumStart;
            check.frameNumberEnd = frameNumEnd;
            check.expectedSamples = NaN;
            check.hasExpected = false;
            check.matchesExpected = false;
            check.toleranceSamples = 1;
            check.sampleCounts = [];

            if isempty(traceDataAll)
                return;
            end

            counts = nan(numel(traceDataAll),1);
            for i = 1:numel(traceDataAll)
                d = traceDataAll{i};
                if isstruct(d) && isfield(d,'values')
                    counts(i) = numel(d.values);
                end
            end
            counts = counts(isfinite(counts));
            check.sampleCounts = counts(:).';
            if isempty(counts)
                return;
            end

            expectedFromFrameNum = NaN;
            if isfinite(frameNumStart) && isfinite(frameNumEnd)
                dFrame = frameNumEnd - frameNumStart;
                if dFrame >= 0
                    expectedFromFrameNum = dFrame;
                end
            end
            expectedFromFramesDone = NaN;
            if isfinite(framesDoneStart) && isfinite(framesDoneEnd)
                dDone = framesDoneEnd - framesDoneStart;
                if dDone >= 0
                    expectedFromFramesDone = dDone;
                end
            end

            if isfinite(expectedFromFrameNum)
                check.expectedSamples = expectedFromFrameNum;
                check.hasExpected = true;
            elseif isfinite(expectedFromFramesDone)
                check.expectedSamples = expectedFromFramesDone;
                check.hasExpected = true;
            end

            if check.hasExpected
                check.matchesExpected = all(abs(counts - check.expectedSamples) <= check.toleranceSamples);
            end
        end

        function framesDone = getFramesDoneSafe(~, hSI)
            framesDone = NaN;
            try
                if most.idioms.isValidObj(hSI) && ~isempty(hSI.hStackManager) && most.idioms.isValidObj(hSI.hStackManager)
                    framesDone = localScalarNumeric(hSI.hStackManager.framesDone, NaN);
                end
            catch
                framesDone = NaN;
            end
        end

        function frameNum = getFrameNumberSafe(~, hSI)
            frameNum = NaN;
            try
                if most.idioms.isValidObj(hSI) && ~isempty(hSI.hDisplay) && most.idioms.isValidObj(hSI.hDisplay)
                    frameNum = localScalarNumeric(hSI.hDisplay.lastFrameNumber, NaN);
                end
            catch
                frameNum = NaN;
            end
        end

        function stopImagingIfStarted(obj, startedImaging, hSI)
            if ~startedImaging
                return;
            end
            try
                if obj.isSiActiveSafe(hSI)
                    hSI.abort();
                end
            catch
            end
        end

        function tf = waitForSiActive(obj, hSI, timeout_s)
            tf = false;
            if nargin < 3 || isempty(timeout_s) || ~isfinite(timeout_s) || timeout_s <= 0
                timeout_s = 2;
            end
            tStart = tic;
            while toc(tStart) < timeout_s
                if obj.isSiActiveSafe(hSI)
                    tf = true;
                    return;
                end
                pause(0.005);
            end
        end

        function tf = waitForPhotostimActive(obj, hPhotostim, timeout_s)
            tf = false;
            if nargin < 3 || isempty(timeout_s) || ~isfinite(timeout_s) || timeout_s <= 0
                timeout_s = 2;
            end
            tStart = tic;
            while toc(tStart) < timeout_s
                if obj.isPhotostimActiveSafe(hPhotostim)
                    tf = true;
                    return;
                end
                drawnow();
                pause(0.005);
            end
        end

        function tf = waitForNextFrameEdge(~, hSI, timeout_s)
            tf = false;
            if nargin < 3 || isempty(timeout_s) || ~isfinite(timeout_s) || timeout_s <= 0
                timeout_s = 2;
            end
            if ~most.idioms.isValidObj(hSI) || ~hSI.active || isempty(hSI.hDisplay) || ~most.idioms.isValidObj(hSI.hDisplay)
                return;
            end

            frame0 = localScalarNumeric(hSI.hDisplay.lastFrameNumber, -inf);
            tStart = tic;
            while toc(tStart) < timeout_s
                pause(0.001);
                if ~most.idioms.isValidObj(hSI) || ~hSI.active
                    break;
                end
                frameNow = localScalarNumeric(hSI.hDisplay.lastFrameNumber, -inf);
                if frameNow ~= frame0
                    tf = true;
                    return;
                end
            end
        end

        function ensureLoggingForStim(~, hSI, hPhotostim)
            assert(most.idioms.isValidObj(hSI) && isprop(hSI,'hChannels') && most.idioms.isValidObj(hSI.hChannels), ...
                'Channels component is not available.');
            assert(most.idioms.isValidObj(hPhotostim), 'Photostim component is not available.');

            try
                if ~hSI.hChannels.loggingEnable
                    hSI.hChannels.loggingEnable = true;
                end
            catch ME
                error('Could not enable imaging logging: %s', ME.message);
            end

            try
                if ~hPhotostim.logging
                    hPhotostim.logging = true;
                end
            catch ME
                if ~hPhotostim.logging
                    error('Could not enable photostim logging: %s', ME.message);
                end
            end

            % Ensure stim timestamps are routed via imaging Aux Trigger 3.
            try
                if isprop(hPhotostim,'pairStimActiveOutputChannel') && ~hPhotostim.pairStimActiveOutputChannel
                    hPhotostim.pairStimActiveOutputChannel = true;
                end
            catch ME
                error('Could not enable Aux Trigger 3 pairing intent: %s', ME.message);
            end

            try
                if ismethod(hPhotostim,'checkOrSetRggScansAuxilliaryTriggerPairing')
                    [stimPaired, ~] = hPhotostim.checkOrSetRggScansAuxilliaryTriggerPairing(true);
                    if ~stimPaired
                        [stimPaired, ~] = hPhotostim.checkOrSetRggScansAuxilliaryTriggerPairing(false);
                    end
                    assert(stimPaired, ['Stim active output is not paired to imaging Aux Trigger 3. ' ...
                        'Configure Photostim advanced trigger pairing and verify hardware routing.']);
                end
            catch ME
                error('Could not verify Aux Trigger 3 pairing: %s', ME.message);
            end
        end

        function restoreStimTriggerTermSafe(~, hPhotostim, trigTerm)
            try
                if most.idioms.isValidObj(hPhotostim)
                    hPhotostim.stimTriggerTerm = trigTerm;
                end
            catch
            end
        end

        function hSI = resolveSI(obj)
            hSI = obj.hModel;
            if ~most.idioms.isValidObj(hSI)
                hSI = [];
            end

            if isempty(hSI)
                try
                    if dabs.resources.ResourceStore.isInstantiated()
                        rs = dabs.resources.ResourceStore();
                        hSI = rs.filterByClass('scanimage.SI');
                        if iscell(hSI)
                            if ~isempty(hSI)
                                hSI = hSI{1};
                            else
                                hSI = [];
                            end
                        end
                    end
                catch
                    hSI = [];
                end
            end

            if isempty(hSI)
                try
                    if evalin('base','exist(''hSI'',''var'')')
                        hSI = evalin('base','hSI');
                    end
                catch
                    hSI = [];
                end
            end

            assert(most.idioms.isValidObj(hSI),'ScanImage is not running.');
        end

        function tf = isSiActiveSafe(~, hSI)
            tf = false;
            try
                if ~most.idioms.isValidObj(hSI)
                    return;
                end
                if isprop(hSI, 'active')
                    tf = localToLogical(hSI.active);
                    if tf
                        return;
                    end
                end
                if isprop(hSI, 'acqState')
                    s = hSI.acqState;
                    if isstring(s)
                        s = char(s);
                    end
                    if ischar(s)
                        tf = any(strcmpi(s, {'focus','grab','loop','loop_wait'}));
                    end
                end
            catch
                tf = false;
            end
        end

        function tf = isPhotostimActiveSafe(~, hPhotostim)
            tf = false;
            try
                if ~most.idioms.isValidObj(hPhotostim)
                    return;
                end
                tf = localToLogical(hPhotostim.active);
            catch
                tf = false;
            end
        end

        function center = getCenter(obj)
            validateattributes(obj.centerX_um, {'numeric'}, {'scalar','finite','real','nonnan'});
            validateattributes(obj.centerY_um, {'numeric'}, {'scalar','finite','real','nonnan'});
            center = [obj.centerX_um obj.centerY_um];
        end

        function setStatus(obj, msg)
            if isempty(obj.etStatus) || ~most.idioms.isValidObj(obj.etStatus)
                return;
            end
            obj.etStatus.String = msg;
        end

        function name = sanitizeFilename(~, name)
            name = regexprep(name, '[<>:"/\\|?*]', '_');
            name = regexprep(name, '\s+', '_');
            name = regexprep(name, '\.+$', '');
            name = strtrim(name);
        end
    end
end

function [stimTimes, source] = localResolveStimTriggerTimes(stimData)
% Prefer hardware trigger times when available.

    stimTimes = [];
    source = 'none';

    if isfield(stimData, 'stim_trigger_times_hw_s') && ~isempty(stimData.stim_trigger_times_hw_s)
        stimTimes = stimData.stim_trigger_times_hw_s(:);
        source = 'stim_trigger_times_hw_s';
    elseif isfield(stimData, 'stim_timing') && isstruct(stimData.stim_timing) ...
            && isfield(stimData.stim_timing, 'hardware_times_s') && ~isempty(stimData.stim_timing.hardware_times_s)
        stimTimes = stimData.stim_timing.hardware_times_s(:);
        source = 'stim_timing.hardware_times_s';
    elseif isfield(stimData, 'stim_trigger_times_s') && ~isempty(stimData.stim_trigger_times_s)
        stimTimes = stimData.stim_trigger_times_s(:);
        source = 'stim_trigger_times_s';
    end

    if isempty(stimTimes)
        return;
    end

    stimTimes = double(stimTimes(:));
    stimTimes = stimTimes(isfinite(stimTimes));
end

function spanFlags = localResolveStimSpanFlags(stimData)
% true: stimulation spanned multiple frames, interpolate 0 and +1
% false: stimulation stayed within one frame, interpolate 0 only

    spanFlags = [];

    if isfield(stimData, 'stim_trigger_times_hw_spans_multiple_frames') && ...
            ~isempty(stimData.stim_trigger_times_hw_spans_multiple_frames)
        spanFlags = logical(stimData.stim_trigger_times_hw_spans_multiple_frames(:));
        return;
    end

    if isfield(stimData, 'stim_timing') && isstruct(stimData.stim_timing) && ...
            isfield(stimData.stim_timing, 'hardware_spans_multiple_frames') && ...
            ~isempty(stimData.stim_timing.hardware_spans_multiple_frames)
        spanFlags = logical(stimData.stim_timing.hardware_spans_multiple_frames(:));
        return;
    end

    if isfield(stimData, 'stim_trigger_times_hw_frame_index') && ...
            isfield(stimData, 'stim_trigger_times_hw_end_frame_index') && ...
            ~isempty(stimData.stim_trigger_times_hw_frame_index) && ...
            ~isempty(stimData.stim_trigger_times_hw_end_frame_index)
        s = double(stimData.stim_trigger_times_hw_frame_index(:));
        e = double(stimData.stim_trigger_times_hw_end_frame_index(:));
        n = min(numel(s), numel(e));
        if n > 0
            spanFlags = (e(1:n) > s(1:n));
        end
    end
end

function [stimTimes, trigSelIdx] = localStimTimesForTrace(trace, traceIdx, stimTimesAll, perTrig, stimData)
    stimTimes = stimTimesAll(:);
    trigSelIdx = (1:numel(stimTimes)).';
    if isempty(stimTimesAll) || isempty(perTrig)
        return;
    end

    thisCenterIdx = [];
    if isfield(trace, 'center_index') && ~isempty(trace.center_index) && isfinite(trace.center_index)
        thisCenterIdx = trace.center_index;
    elseif isfield(stimData, 'centers_um') && isfield(trace, 'center_um') && ~isempty(stimData.centers_um) ...
            && ~isempty(trace.center_um) && numel(trace.center_um) >= 2
        try
            thisCenterIdx = find(all(abs(stimData.centers_um - trace.center_um(1:2)) < 1e-6, 2), 1, 'first');
        catch
            thisCenterIdx = [];
        end
    end
    if isempty(thisCenterIdx)
        thisCenterIdx = traceIdx;
    end

    n = min(numel(stimTimesAll), numel(perTrig));
    useMask = false(n,1);
    for k = 1:n
        if isstruct(perTrig(k)) && isfield(perTrig(k), 'center_index') && ~isempty(perTrig(k).center_index)
            useMask(k) = perTrig(k).center_index == thisCenterIdx;
        end
    end

    if any(useMask)
        baseIdx = (1:n).';
        stimTimes = stimTimesAll(1:n);
        trigSelIdx = baseIdx(useMask);
        stimTimes = stimTimes(useMask);
    end
end

function yInterp = localInterpolateStimFrames(times, values, stimTimes, stimSpan)
% Replace stimulation artifact frames using quadratic fit from neighboring
% clean frames (-3, -2, -1, +2, +3).
% Single-frame stim: interpolate frame 0 only.
% Multi-frame stim: interpolate frames 0 and +1.
    yInterp = values(:);
    if isempty(stimTimes) || numel(yInterp) < 7
        return;
    end
    if nargin < 4 || isempty(stimSpan)
        stimSpan = true(numel(stimTimes),1);
    end

    n = numel(yInterp);
    frameIdx = (0:n-1).';
    stimTimes = stimTimes(:);
    stimSpan = logical(stimSpan(:));
    if numel(stimSpan) < numel(stimTimes)
        stimSpan(end+1:numel(stimTimes),1) = true;
    elseif numel(stimSpan) > numel(stimTimes)
        stimSpan = stimSpan(1:numel(stimTimes));
    end

    stimFrameEstimate = interp1(times(:), frameIdx, stimTimes, 'linear', NaN);
    validStim = isfinite(stimFrameEstimate);
    stimFrames = floor(stimFrameEstimate(validStim));
    stimSpan = stimSpan(validStim);
    inRange = stimFrames >= 0 & stimFrames <= (n-1);
    stimFrames = stimFrames(inRange);
    stimSpan = stimSpan(inRange);
    if isempty(stimFrames)
        return;
    end

    [stimFramesUnique, ~, stimGroup] = unique(stimFrames(:));
    spanByFrame = false(numel(stimFramesUnique),1);
    for i = 1:numel(stimFramesUnique)
        spanByFrame(i) = any(stimSpan(stimGroup == i));
    end

    fitRel = [-3 -2 -1 2 3];
    targetMask = false(n,1);
    for i = 1:numel(stimFramesUnique)
        s = stimFramesUnique(i);
        targetRel = 0;
        if spanByFrame(i)
            targetRel = [0 1];
        end
        d = s + targetRel;
        d = d(d >= 0 & d <= (n-1));
        targetMask(d + 1) = true;
    end

    for i = 1:numel(stimFramesUnique)
        s = stimFramesUnique(i);
        targetRel = 0;
        if spanByFrame(i)
            targetRel = [0 1];
        end
        src = s + fitRel;
        dst = s + targetRel;
        if any(src < 0) || any(src > (n-1)) || any(dst < 0) || any(dst > (n-1))
            continue;
        end
        if any(targetMask(src + 1))
            continue;
        end
        srcVals = values(src + 1);
        if any(~isfinite(srcVals))
            continue;
        end
        p = polyfit(fitRel, srcVals(:).', 2);
        yInterp(dst + 1) = polyval(p, targetRel);
    end
end

function tf = localToLogical(v)
    tf = false;
    if isempty(v)
        return;
    end
    if islogical(v)
        tf = any(v(:));
        return;
    end
    if isnumeric(v)
        tf = any(v(:) ~= 0);
        return;
    end
    if isstring(v)
        if isscalar(v)
            v = char(v);
        else
            tf = any(v ~= "");
            return;
        end
    end
    if ischar(v)
        s = strtrim(lower(v));
        if isempty(s) || strcmp(s,'idle') || strcmp(s,'false') || strcmp(s,'0') || strcmp(s,'off')
            tf = false;
        else
            tf = true;
        end
        return;
    end
    try
        tf = logical(v);
        if ~isscalar(tf)
            tf = any(tf(:));
        end
    catch
        tf = false;
    end
end

function v = localScalarNumeric(x, defaultValue)
    if nargin < 2
        defaultValue = NaN;
    end
    v = defaultValue;
    if isempty(x)
        return;
    end
    if islogical(x)
        x = double(x);
    end
    if ~isnumeric(x)
        try
            x = double(x);
        catch
            return;
        end
    end
    x = x(:);
    x = x(isfinite(x));
    if isempty(x)
        return;
    end
    v = x(end);
end
