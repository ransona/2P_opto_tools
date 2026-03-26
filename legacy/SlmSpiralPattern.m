classdef SlmSpiralPattern < most.Gui
    properties (SetObservable)
        centerX_um = 0;
        centerY_um = 0;
        centerX_list = '0';
        centerY_list = '0';
        animal_name = 'ESYB';
        spiralDiameter_um = 15;
        power_start_pct = 5;
        power_stop_pct = 30;
        duration_start_ms = 10;
        duration_stop_ms = 20;
        rangeStep = 5;
        interStimGap_ms = 2000;
        sequence_repetitions = 10;
        preStimDelay_ms = 10000;
        postStimDelay_ms = 10000;
        prePause_ms = 1;
        postPark_ms = 1;
        stimTriggerOnFrame = true;
        forceEnableLoggingForStim = true;
    end

    properties (Hidden)
        revolutions = 5;
        galvoOffsetX_um = 20;
        galvoOffsetY_um = 20;
        grabFramePadding_s = 60*30;
        maxCellsForPhotostimLogging = 4;
    end

    properties (Access = private)
        etStatus;
        etEstimate;
        hAutoAbortTimer;
        hEstimateListeners = event.proplistener.empty(0,1);
        lastSavePath = '';
        hCh1Traces = {};
        hFrameTraceListener = [];
        hFrameTraceTimer = [];
        lastTraceFrameNumber = -inf;
        useIntegrationLogging = false;
        integrationConfigBackup = struct('valid',false);
        traceFramesDoneStart = NaN;
        traceFramesDoneEnd = NaN;
        traceFrameNumberStart = NaN;
        traceFrameNumberEnd = NaN;
        lastCellPickerRedImage = [];
        lastCellPickerRedChannel = NaN;
        lastCellPickerRedTimestamp = '';
        lastCellPickerRedPath = '';
        abortRequested = false;
        runInProgress = false;
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
        function obj = SlmSpiralPattern(hModel, hController)
            if nargin < 1
                hModel = [];
            end
            if nargin < 2
                hController = [];
            end
            obj@most.Gui(hModel, hController, [640 480], 'pixels');
        end
    end

    methods (Access = protected)
        function initGui(obj)
            set(obj.hFig,'Name','Cell Stimulator','Resize','on');

            mainFlow = most.gui.uiflowcontainer('Parent',obj.hFig,'FlowDirection','TopDown');
            panel = uipanel('Parent',mainFlow,'Title','Spiral Parameters');
            panelFlow = most.gui.uiflowcontainer('Parent',panel,'FlowDirection','TopDown');

            addRowList(panelFlow, 'Centers X (um)', obj, 'centerX_list');
            addRowList(panelFlow, 'Centers Y (um)', obj, 'centerY_list');
            addRow(panelFlow, 'Animal Name', obj, 'animal_name', 'string');
            addRow(panelFlow, 'Spiral Diameter (um)', obj, 'spiralDiameter_um');
            addRow(panelFlow, 'Power Start (%)', obj, 'power_start_pct');
            addRow(panelFlow, 'Power Stop (%)', obj, 'power_stop_pct');
            addRow(panelFlow, 'Duration Start (ms)', obj, 'duration_start_ms');
            addRow(panelFlow, 'Duration Stop (ms)', obj, 'duration_stop_ms');
            addRow(panelFlow, 'Step (Pwr/Dur)', obj, 'rangeStep');
            addRow(panelFlow, 'Inter-Stim Gap (ms)', obj, 'interStimGap_ms');
            addRow(panelFlow, 'Sequence Repetitions', obj, 'sequence_repetitions');
            addRow(panelFlow, 'Pre-Stim Delay (ms)', obj, 'preStimDelay_ms');
            addRow(panelFlow, 'Post-Stim Delay (ms)', obj, 'postStimDelay_ms');
            addRowCheckbox(panelFlow, 'Trigger On Frame', obj, 'stimTriggerOnFrame');
            addRowCheckbox(panelFlow, 'Enable Logging', obj, 'forceEnableLoggingForStim');

            buttonFlow = most.gui.uiflowcontainer('Parent',mainFlow,'FlowDirection','LeftToRight','HeightLimits',[24 24]);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Create Stim Group','Callback',@obj.createStimGroup);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Stimulate Now','Callback',@obj.stimulateNow);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Abort','Callback',@obj.abortStimulateNow);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Cell Picker','Callback',@obj.pickCentersFromImage);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Run PSTH','Callback',@obj.runPsth);
            obj.addUiControl('Parent',buttonFlow,'Style','pushbutton','String','Close','Callback',@(~,~)delete(obj));

            statusFlow = most.gui.uiflowcontainer('Parent',mainFlow,'FlowDirection','LeftToRight','HeightLimits',[18 18]);
            obj.etStatus = obj.addUiControl('Parent',statusFlow,'Style','text','String','Ready','HorizontalAlignment','left');

            estimateFlow = most.gui.uiflowcontainer('Parent',mainFlow,'FlowDirection','LeftToRight','HeightLimits',[18 18]);
            most.gui.uicontrol('Parent',estimateFlow,'Style','text','String','Estimated time','WidthLimits',[120 120], ...
                'HorizontalAlignment','right');
            obj.etEstimate = obj.addUiControl('Parent',estimateFlow,'Style','text','String','--','HorizontalAlignment','left');

            props = {'centerX_list','centerY_list','power_start_pct','power_stop_pct', ...
                'duration_start_ms','duration_stop_ms','rangeStep','interStimGap_ms','sequence_repetitions', ...
                'preStimDelay_ms','postStimDelay_ms','prePause_ms','postPark_ms'};
            for i = 1:numel(props)
                obj.hEstimateListeners(end+1) = addlistener(obj, props{i}, 'PostSet', @(~,~)obj.updateEstimate()); %#ok<AGROW>
            end
            obj.hEstimateListeners(end+1) = addlistener(obj, 'forceEnableLoggingForStim', ...
                'PostSet', @(~,~)obj.applyLoggingPreference()); %#ok<AGROW>
            obj.updateEstimate();
            obj.applyLoggingPreference();

            function addRow(parent, label, model, prop, bindingType)
                if nargin < 5 || isempty(bindingType)
                    bindingType = 'value';
                end
                row = most.gui.uiflowcontainer('Parent',parent,'FlowDirection','LeftToRight','HeightLimits',[22 22]);
                most.gui.uicontrol('Parent',row,'Style','text','String',label,'WidthLimits',[120 120], ...
                    'HorizontalAlignment','right');
                most.gui.uicontrol('Parent',row,'Style','edit','Bindings',{model prop bindingType});
            end

            function addRowList(parent, label, model, prop)
                row = most.gui.uiflowcontainer('Parent',parent,'FlowDirection','LeftToRight','HeightLimits',[22 22]);
                most.gui.uicontrol('Parent',row,'Style','text','String',label,'WidthLimits',[120 120], ...
                    'HorizontalAlignment','right');
                hEdit = most.gui.uicontrol('Parent',row,'Style','edit','String',model.(prop), ...
                    'Callback',@(src,~)set(model, prop, get(src,'String')));
                obj.hEstimateListeners(end+1) = addlistener(model, prop, 'PostSet', @(~,~)set(hEdit,'String', model.(prop))); %#ok<AGROW>
            end

            function addRowCheckbox(parent, label, model, prop)
                row = most.gui.uiflowcontainer('Parent',parent,'FlowDirection','LeftToRight','HeightLimits',[22 22]);
                most.gui.uicontrol('Parent',row,'Style','text','String',label,'WidthLimits',[120 120], ...
                    'HorizontalAlignment','right');
                most.gui.uicontrol('Parent',row,'Style','checkbox','Bindings',{model prop 'value'});
            end
        end
    end

    methods
        function delete(obj)
            obj.clearRunState();
            obj.stopChannel1Logging();
            obj.clearAutoAbortTimer();
            delete@most.Gui(obj);
        end

        function [idx, rg, groupInfo] = createStimGroup(obj,varargin)
            idx = [];
            rg = scanimage.mroi.RoiGroup.empty(1,0);
            groupInfo = repmat(struct('center_index',[],'center_x_um',[],'center_y_um',[], ...
                'power_index',[],'duration_index',[],'combo_index',[],'linear_combo_index',[], ...
                'group_index',[],'power_pct',[],'duration_ms',[]), 0, 1);
            try
                hSI = obj.resolveSI();
                hPhotostim = hSI.hPhotostim;

                assert(most.idioms.isValidObj(hPhotostim),'Photostim component is not available.');
                assert(~obj.isPhotostimActiveSafe(hPhotostim),'Stop Photostim before editing stim groups.');
                assert(hPhotostim.numInstances > 0,'Photostim is not configured.');
                assert(hPhotostim.hasSlm,'No SLM available in the stimulation scannerset.');

                hPhotostim.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1,0);
                hPhotostim.sequenceSelectedStimuli = [];

                validateattributes(obj.spiralDiameter_um,{'numeric'},{'scalar','positive','finite','nonnan'});
                centers = obj.getCenterList();
                [powerVals, durationVals] = obj.getPowerDurationVals();
                validateattributes(obj.sequence_repetitions,{'numeric'},{'scalar','finite','real','nonnan','>=',1});
                seqReps = round(obj.sequence_repetitions);

                assert(~isempty(hSI.objectiveResolution),'objectiveResolution is not set in ScanImage.');

                sizeDeg = obj.spiralDiameter_um ./ hSI.objectiveResolution;
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

                multiCombo = numel(powerVals) > 1 || numel(durationVals) > 1 || size(centers,1) > 1;
                nD = numel(durationVals);
                for iC = 1:size(centers,1)
                    center_um = centers(iC,:);
                    centerDeg = (center_um + [obj.galvoOffsetX_um obj.galvoOffsetY_um]) ./ hSI.objectiveResolution;

                    for iP = 1:numel(powerVals)
                        for iD = 1:numel(durationVals)
                            power_pct = powerVals(iP);
                            duration_ms = durationVals(iD);

                            sf = scanimage.mroi.scanfield.fields.StimulusField();
                            stimRois = scanimage.mroi.Roi.empty(1,0);
                            sf.centerXY = centerDeg;
                            sf.sizeXY = [sizeDeg sizeDeg];
                            sf.duration = duration_ms / 1000;
                            sf.repetitions = 1;
                            sf.stimfcnhdl = @scanimage.mroi.stimulusfunctions.logspiral;
                            sf.stimparams = {'revolutions', obj.revolutions, 'direction', 'outward'};
                            sf.slmPattern = slmPattern;

                            powers = zeros(1,nBeams);
                            powers(3) = power_pct;
                            sf.powers = powers;
                            roi = scanimage.mroi.Roi();
                            roi.add(0, sf);
                            stimRois(end+1) = roi;

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
                                stimRois = [roiPause stimRois];
                            end

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
                                stimRois(end+1) = roiPark;
                            end

                            baseName = sprintf('SLM Spiral C%d (%.1f, %.1f)', iC, center_um(1), center_um(2));
                            if multiCombo
                                rgName = sprintf('%s P%.1f D%.1f', baseName, power_pct, duration_ms);
                            else
                                rgName = baseName;
                            end
                            rgLocal = scanimage.mroi.RoiGroup(rgName);
                            for iRoi = 1:numel(stimRois)
                                rgLocal.add(stimRois(iRoi));
                            end

                            if isempty(hPhotostim.stimRoiGroups)
                                hPhotostim.stimRoiGroups = rgLocal;
                                idx(end+1) = 1;
                            else
                                names = {hPhotostim.stimRoiGroups.name};
                                match = find(strcmp(names, rgName),1,'first');
                                if ~isempty(match)
                                    hPhotostim.stimRoiGroups(match) = rgLocal;
                                    idx(end+1) = match;
                                else
                                    hPhotostim.stimRoiGroups(end+1) = rgLocal;
                                    idx(end+1) = numel(hPhotostim.stimRoiGroups);
                                end
                            end

                            info = struct();
                            info.center_index = iC;
                            info.center_x_um = center_um(1);
                            info.center_y_um = center_um(2);
                            info.power_index = iP;
                            info.duration_index = iD;
                            info.combo_index = [iP iD];
                            info.linear_combo_index = (iP-1) * nD + iD;
                            info.group_index = idx(end);
                            info.power_pct = power_pct;
                            info.duration_ms = duration_ms;
                            groupInfo(end+1,1) = info; %#ok<AGROW>

                            rg(end+1) = rgLocal;
                        end
                    end
                end

                if ~isempty(idx)
                    hPhotostim.stimulusMode = 'sequence';
                    hPhotostim.sequenceSelectedStimuli = idx(:).';
                    hPhotostim.numSequences = seqReps;
                end

                if numel(idx) == 1
                    obj.setStatus(sprintf('Created stim group: %s (sequence set)',rg.name));
                else
                    obj.setStatus(sprintf('Created %d stim groups (sequence set).', numel(idx)));
                end
            catch ME
                obj.setStatus(sprintf('Error: %s',ME.message));
                most.ErrorHandler.logAndReportError(ME);
            end
        end

        function abortStimulateNow(obj, varargin)
            if ~obj.runInProgress
                obj.setStatus('No active stimulation run.');
                return;
            end

            obj.abortRequested = true;
            obj.setStatus('Abort requested. Saving data collected so far...');

            try
                hSI = obj.resolveSI();
                hPhotostim = hSI.hPhotostim;
                if most.idioms.isValidObj(hPhotostim) && obj.isPhotostimActiveSafe(hPhotostim)
                    hPhotostim.abort();
                end
            catch
            end
        end

        function stimulateNow(obj,varargin)
            obj.clearRunState();
            obj.runInProgress = true;
            runStateCleanup = onCleanup(@()obj.clearRunState()); %#ok<NASGU>

            hSI = [];
            hPhotostim = [];
            startedImaging = false;
            oldStimTriggerTerm = '';
            oldPhotostimLogging = [];
            oldPhotostimMonitoring = [];
            photostimLoggingTemporarilyDisabled = false;
            photostimMonitoringTemporarilyDisabled = false;
            logRestoreMsg = '';
            traceDataAll = {};
            traceLengthCheck = struct();
            runCfg = obj.emptyGrabRunConfig();
            plannedSavePath = '';
            saveBaseName = '';
            saveDir = '';
            greenTiffPath = '';
            try
                hSI = obj.resolveSI();
                hPhotostim = hSI.hPhotostim;

                if obj.isPhotostimActiveSafe(hPhotostim)
                    try
                        hPhotostim.abort();
                        pause(0.05);
                    catch
                    end
                end
                assert(~obj.isPhotostimActiveSafe(hPhotostim), 'Abort Photostim before triggering.');
                if obj.forceEnableLoggingForStim
                    obj.ensureLoggingForStim(hSI, hPhotostim);
                end
                [plannedSavePath, saveBaseName, saveDir] = obj.reserveSessionSavePath();

                try
                    oldStimTriggerTerm = hPhotostim.stimTriggerTerm;
                catch
                    oldStimTriggerTerm = '';
                end
                if ~obj.isPhotostimActiveSafe(hPhotostim)
                    hPhotostim.stimTriggerTerm = '';
                end

                [idx, ~, groupInfo] = obj.createStimGroup();
                if isempty(idx)
                    obj.stopChannel1Logging();
                    obj.stopImagingIfStarted(startedImaging, hSI);
                    obj.restorePhotostimMonitoringSafe(hPhotostim, oldPhotostimMonitoring, photostimMonitoringTemporarilyDisabled);
                    obj.restorePhotostimLoggingSafe(hPhotostim, oldPhotostimLogging, photostimLoggingTemporarilyDisabled);
                    obj.restoreStimTriggerTermSafe(hPhotostim, oldStimTriggerTerm);
                    return;
                end

                centers = obj.getCenterList();
                nCentersRequested = size(centers,1);
                oldPhotostimLogging = obj.getPhotostimLoggingSafe(hPhotostim);
                oldPhotostimMonitoring = obj.getPhotostimMonitoringSafe(hPhotostim);
                if nCentersRequested > obj.maxCellsForPhotostimLogging
                    if oldPhotostimMonitoring
                        photostimMonitoringTemporarilyDisabled = obj.setPhotostimMonitoringSafe(hPhotostim, false);
                    end
                    if oldPhotostimLogging
                        photostimLoggingTemporarilyDisabled = obj.setPhotostimLoggingSafe(hPhotostim, false);
                    end
                    if photostimMonitoringTemporarilyDisabled || photostimLoggingTemporarilyDisabled
                        obj.setStatus(sprintf(['Temporarily disabled photostim monitoring/logging ' ...
                            'for %d cells to keep acquisition stable.'], nCentersRequested));
                    end
                end

                [powerVals, durationVals] = obj.getPowerDurationVals();
                validateattributes(obj.interStimGap_ms,{'numeric'},{'scalar','finite','real','nonnan','>=',0});
                validateattributes(obj.sequence_repetitions,{'numeric'},{'scalar','finite','real','nonnan','>=',1});
                seqReps = round(obj.sequence_repetitions);

                [triggerPlan, idxTriggerList, nCenters] = obj.buildTriggerPlan(groupInfo, seqReps);
                totalTriggers = numel(idxTriggerList);
                assert(totalTriggers > 0, 'No trigger plan generated.');
                estRun_s = obj.estimateRunDuration_s(triggerPlan, totalTriggers, obj.getFrameRateSafe(hSI));

                obj.startChannel1Logging(hSI, centers);

                if ~obj.isSiActiveSafe(hSI)
                    runCfg = obj.configureGrabForGreenTiff(hSI, saveBaseName, saveDir, estRun_s);
                    startedImaging = runCfg.startedAcq;
                end
                obj.traceFramesDoneStart = obj.getFramesDoneSafe(hSI);
                obj.traceFrameNumberStart = obj.getFrameNumberSafe(hSI);
                if obj.stimTriggerOnFrame
                    assert(obj.isSiActiveSafe(hSI), 'Trigger On Frame requires active imaging (grab/loop/acquisition).');
                end

                hPhotostim.stimulusMode = 'sequence';
                hPhotostim.sequenceSelectedStimuli = idxTriggerList(:).';
                hPhotostim.numSequences = 1;
                hPhotostim.autoTriggerPeriod = 0;
                hPhotostim.stimImmediately = false;
                hPhotostim.start();

                validateattributes(obj.preStimDelay_ms,{'numeric'},{'scalar','finite','real','nonnan','>=',0});
                preDelay_s = obj.preStimDelay_ms / 1000;
                total_s = 0;
                if preDelay_s > 0
                    [waitedPre_s, abortedDuringWait] = obj.pauseWithAbort(preDelay_s, hPhotostim);
                    total_s = total_s + waitedPre_s;
                    if abortedDuringWait && obj.abortRequested
                        obj.setStatus('Abort requested. Saving data collected so far...');
                    end
                end

                gap_s = obj.interStimGap_ms / 1000;
                stimTriggerTimes = zeros(totalTriggers,1);
                tRun = tic;

                nDone = 0;
                for t = 1:totalTriggers
                    if obj.abortRequested || ~most.idioms.isValidObj(hPhotostim) || ~obj.isPhotostimActiveSafe(hPhotostim)
                        break;
                    end

                    entry = triggerPlan(t);
                    if obj.stimTriggerOnFrame
                        total_s = total_s + obj.waitForNextFrameEdge(hSI, 2);
                        if obj.abortRequested || ~most.idioms.isValidObj(hPhotostim) || ~obj.isPhotostimActiveSafe(hPhotostim)
                            break;
                        end
                    end

                    stimT = [];
                    for tr = 1:numel(obj.hCh1Traces)
                        hTrace = obj.hCh1Traces{tr};
                        if ~isempty(hTrace) && isvalid(hTrace)
                            tLocal = hTrace.markStim();
                            if isempty(stimT)
                                stimT = tLocal;
                            end
                        end
                    end

                    hPhotostim.triggerStim();
                    nDone = nDone + 1;
                    if isempty(stimT)
                        stimT = toc(tRun);
                    end
                    stimTriggerTimes(nDone,1) = stimT;

                    stim_s = (obj.prePause_ms + entry.duration_ms + obj.postPark_ms) / 1000;
                    if t < totalTriggers
                        [waited_s, abortedDuringWait] = obj.pauseWithAbort(stim_s + gap_s, hPhotostim);
                        total_s = total_s + waited_s;
                        if abortedDuringWait
                            break;
                        end
                    else
                        [waited_s, ~] = obj.pauseWithAbort(stim_s, hPhotostim);
                        total_s = total_s + waited_s;
                    end
                end

                stimTriggerTimes = stimTriggerTimes(1:nDone,:);
                stimParamList = triggerPlan(1:nDone);
                abortedByUser = obj.abortRequested;

                validateattributes(obj.postStimDelay_ms,{'numeric'},{'scalar','finite','real','nonnan','>=',0});
                postDelay_s = obj.postStimDelay_ms / 1000;
                if postDelay_s > 0 && ~abortedByUser
                    [waitedPost_s, ~] = obj.pauseWithAbort(postDelay_s, []);
                    total_s = total_s + waitedPost_s;
                end

                obj.traceFramesDoneEnd = obj.getFramesDoneSafe(hSI);
                obj.traceFrameNumberEnd = obj.getFrameNumberSafe(hSI);
                obj.stopImagingIfStarted(startedImaging, hSI);
                obj.restoreGrabRunConfig(hSI, runCfg);
                traceDataAll = obj.collectTraceData();
                traceLengthCheck = obj.buildTraceLengthCheck(traceDataAll);
                obj.stopChannel1Logging();
                obj.startAutoAbortTimer(total_s + 0.1, hPhotostim);
                greenTiffPath = obj.finalizeGreenTiff(runCfg, plannedSavePath);
                savePath = obj.saveSessionData(stimTriggerTimes, powerVals, durationVals, idx, stimParamList, ...
                    traceDataAll, traceLengthCheck, plannedSavePath, greenTiffPath);
                if ~isempty(savePath)
                    obj.lastSavePath = savePath;
                end

                obj.restorePhotostimMonitoringSafe(hPhotostim, oldPhotostimMonitoring, photostimMonitoringTemporarilyDisabled);
                [~, logRestoreMsg] = obj.restorePhotostimLoggingSafe(hPhotostim, oldPhotostimLogging, photostimLoggingTemporarilyDisabled);
                obj.restoreStimTriggerTermSafe(hPhotostim, oldStimTriggerTerm);
                logRestoreSuffix = '';
                if ~isempty(logRestoreMsg)
                    logRestoreSuffix = [' ' logRestoreMsg];
                end
                if abortedByUser
                    obj.setStatus(sprintf('Abort requested. Saved partial run (%d/%d triggers, %d cells).%s', ...
                        nDone, totalTriggers, nCenters, logRestoreSuffix));
                elseif isfield(traceLengthCheck,'hasExpected') && traceLengthCheck.hasExpected
                    if traceLengthCheck.matchesExpected
                        obj.setStatus(sprintf(['Triggered sequence (%d/%d triggers, %d cells). ' ...
                            'Trace/frame check OK.%s'], nDone, totalTriggers, nCenters, logRestoreSuffix));
                    else
                        obj.setStatus(sprintf(['Triggered sequence (%d/%d triggers, %d cells). ' ...
                            'Trace/frame mismatch: expected %d, got [%d..%d].%s'], ...
                            nDone, totalTriggers, nCenters, ...
                            traceLengthCheck.expectedSamples, traceLengthCheck.minSamples, traceLengthCheck.maxSamples, ...
                            logRestoreSuffix));
                    end
                else
                    obj.setStatus(sprintf('Triggered sequence (%d/%d triggers, %d cells).%s', ...
                        nDone, totalTriggers, nCenters, logRestoreSuffix));
                end
            catch ME
                obj.stopImagingIfStarted(startedImaging, hSI);
                obj.restoreGrabRunConfig(hSI, runCfg);
                obj.stopChannel1Logging();
                obj.restorePhotostimMonitoringSafe(hPhotostim, oldPhotostimMonitoring, photostimMonitoringTemporarilyDisabled);
                obj.restorePhotostimLoggingSafe(hPhotostim, oldPhotostimLogging, photostimLoggingTemporarilyDisabled);
                obj.restoreStimTriggerTermSafe(hPhotostim, oldStimTriggerTerm);
                obj.setStatus(sprintf('Error: %s',ME.message));
                most.ErrorHandler.logAndReportError(ME);
            end
        end

        function savePath = saveSessionData(obj, stimTriggerTimes, powerVals, durationVals, idx, stimParamList, ...
                traceDataAllInput, traceLengthCheck, preferredSavePath, greenTiffPath)
            savePath = '';
            try
                if nargin < 6
                    stimParamList = repmat(struct('rep',[],'combo_index',[],'linear_combo_index',[], ...
                        'power_index',[],'duration_index',[],'center_index',[], ...
                        'center_x_um',[],'center_y_um',[],'group_index',[], ...
                        'power_pct',[],'duration_ms',[]), 0, 1);
                end
                if nargin < 7 || isempty(traceDataAllInput)
                    traceDataAll = {};
                elseif iscell(traceDataAllInput)
                    traceDataAll = traceDataAllInput;
                else
                    traceDataAll = {traceDataAllInput};
                end
                if nargin < 8 || isempty(traceLengthCheck)
                    traceLengthCheck = struct();
                end
                if nargin < 9
                    preferredSavePath = '';
                end
                if nargin < 10
                    greenTiffPath = '';
                end

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

                if ~isempty(preferredSavePath)
                    savePath = preferredSavePath;
                else
                    saveDir = pwd;
                    savePath = fullfile(saveDir, [baseName '.mat']);
                    if exist(savePath, 'file')
                        stamp = datestr(now,'yyyymmdd_HHMMSS');
                        savePath = fullfile(saveDir, sprintf('%s_%s.mat', baseName, stamp));
                    end
                end

                params = struct();
                params.centerX_um = obj.centerX_um;
                params.centerY_um = obj.centerY_um;
                params.centerX_list = obj.centerX_list;
                params.centerY_list = obj.centerY_list;
                params.spiralDiameter_um = obj.spiralDiameter_um;
                params.power_start_pct = obj.power_start_pct;
                params.power_stop_pct = obj.power_stop_pct;
                params.duration_start_ms = obj.duration_start_ms;
                params.duration_stop_ms = obj.duration_stop_ms;
                params.rangeStep = obj.rangeStep;
                params.interStimGap_ms = obj.interStimGap_ms;
                params.sequence_repetitions = obj.sequence_repetitions;
                params.preStimDelay_ms = obj.preStimDelay_ms;
                params.postStimDelay_ms = obj.postStimDelay_ms;
                params.prePause_ms = obj.prePause_ms;
                params.postPark_ms = obj.postPark_ms;
                params.revolutions = obj.revolutions;
                params.galvoOffsetX_um = obj.galvoOffsetX_um;
                params.galvoOffsetY_um = obj.galvoOffsetY_um;
                params.grabFramePadding_s = obj.grabFramePadding_s;
                params.maxCellsForPhotostimLogging = obj.maxCellsForPhotostimLogging;
                params.stimTriggerOnFrame = obj.stimTriggerOnFrame;
                params.forceEnableLoggingForStim = obj.forceEnableLoggingForStim;

                stimData = struct();
                stimData.animal_name = name;
                stimData.timestamp = datestr(now,'yyyy-mm-dd HH:MM:SS');
                stimData.params = params;
                stimData.power_values_pct = powerVals;
                stimData.duration_values_ms = durationVals;
                stimData.stimulus_indices = idx;
                stimData.stim_trigger_times_s = stimTriggerTimes;
                stimData.stim_per_trigger = stimParamList;
                stimData.trace_length_check = traceLengthCheck;
                stimData.green_tiff_path = greenTiffPath;
                stimData.green_last20_mean_image = [];
                stimData.green_last20_mean_n_frames = 0;
                stimData.green_last20_total_frames = 0;
                stimData.green_last20_frame_indices = [];
                stimData.cell_picker_red_image = obj.lastCellPickerRedImage;
                stimData.cell_picker_red_channel = obj.lastCellPickerRedChannel;
                stimData.cell_picker_red_timestamp = obj.lastCellPickerRedTimestamp;
                stimData.cell_picker_red_path = obj.lastCellPickerRedPath;
                if isempty(traceDataAll)
                    stimData.trace = [];
                    stimData.traces = {};
                else
                    stimData.traces = traceDataAll;
                    stimData.trace = traceDataAll{1};
                end
                try
                    stimData.centers_um = obj.getCenterList();
                catch
                end

                % Save session payload directly; no TIFF post-processing.
                save(savePath, 'stimData');
            catch ME
                obj.setStatus(sprintf('Save failed: %s', ME.message));
            end
        end
    end

    methods (Access = private)
        function startChannel1Logging(obj, hSI, centers)
            obj.stopChannel1Logging();
            obj.closeLegacyTraceFigures();
            if isempty(centers)
                return;
            end

            obj.traceFramesDoneStart = obj.getFramesDoneSafe(hSI);
            obj.traceFrameNumberStart = obj.getFrameNumberSafe(hSI);
            obj.traceFramesDoneEnd = NaN;
            obj.traceFrameNumberEnd = NaN;

            obj.useIntegrationLogging = obj.configureIntegrationRoisForLogging(hSI, centers);

            nCenters = size(centers,1);
            traceDiameterUm = obj.getTraceRoiDiameterUm();
            obj.hCh1Traces = cell(nCenters,1);
            for i = 1:nCenters
                hTrace = scanimage.guis.SlmSpiralPatternChannel1Trace(hSI, centers(i,:), traceDiameterUm, [], i, false);
                hTrace.start(false, ~obj.useIntegrationLogging);
                obj.hCh1Traces{i} = hTrace;
            end

            if obj.useIntegrationLogging
                obj.setStatus(sprintf('Logging mode: Integration ROI (%d cells).', nCenters));
            else
                obj.setStatus(sprintf('Logging mode: frame extraction fallback (%d cells).', nCenters));
            end

            obj.startFrameTraceUpdates(hSI);
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
            check.minSamples = NaN;
            check.maxSamples = NaN;

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
            check.minSamples = min(counts);
            check.maxSamples = max(counts);

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
            else
                check.expectedSamples = NaN;
                check.hasExpected = false;
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

            saveDir = pwd;
            savePath = fullfile(saveDir, [baseName '.mat']);
            tiffPath = fullfile(saveDir, [baseName '.tif']);
            if exist(savePath, 'file') || exist(tiffPath, 'file')
                stamp = datestr(now,'yyyymmdd_HHMMSS');
                baseName = sprintf('%s_%s', baseName, stamp);
                savePath = fullfile(saveDir, [baseName '.mat']);
            end
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

        function run_s = estimateRunDuration_s(obj, triggerPlan, totalTriggers, frameRate_Hz)
            run_ms = obj.preStimDelay_ms + obj.postStimDelay_ms;
            if totalTriggers > 0 && ~isempty(triggerPlan)
                perStim_ms = obj.prePause_ms + [triggerPlan.duration_ms] + obj.postPark_ms;
                run_ms = run_ms + sum(perStim_ms);
                if totalTriggers > 1
                    run_ms = run_ms + (totalTriggers - 1) * obj.interStimGap_ms;
                end
                if obj.stimTriggerOnFrame && isfinite(frameRate_Hz) && frameRate_Hz > 0
                    run_ms = run_ms + 1000 * (totalTriggers / frameRate_Hz);
                end
            end
            run_s = max(1, run_ms / 1000 + 0.5);
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
                % Stream one TIFF to disk during this GRAB (append per frame).
                hScan2D.logFramesPerFile = inf;

                frameRate = obj.getFrameRateSafe(hSI);
                pad_s = localScalarNumeric(obj.grabFramePadding_s, 0);
                if ~isfinite(pad_s) || pad_s < 0
                    pad_s = 0;
                end
                nFrames = max(1, ceil((run_s + pad_s) * frameRate));
                try
                    hScan2D.framesPerAcq = nFrames;
                catch MEsetFrames
                    % Some ScanImage scanner classes expose framesPerAcq as
                    % read-only to non-core classes. Fall back to stack
                    % manager framesPerSlice, which is applied internally
                    % at acquisition start.
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

        function restoreGrabRunConfig(obj, hSI, cfg)
            if ~isstruct(cfg)
                return;
            end
            if ~most.idioms.isValidObj(hSI) || isempty(hSI.hScan2D) || ~most.idioms.isValidObj(hSI.hScan2D)
                return;
            end
            if obj.isSiActiveSafe(hSI)
                return;
            end

            try
                if isfield(cfg,'oldChannelSave') && ~isempty(cfg.oldChannelSave) ...
                        && most.idioms.isValidObj(hSI.hChannels)
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
                if ~isempty(f)
                    % Keep only files created during this run (plus small margin for clock granularity).
                    if isfield(cfg,'tStartDatenum') && isfinite(cfg.tStartDatenum)
                        keep = [f.datenum] >= (cfg.tStartDatenum - 2/86400);
                        f = f(keep);
                    end
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

        function closeLegacyTraceFigures(~)
            try
                figs = findall(0,'Type','figure','-regexp','Name','^SLM Center Ch1 Timeseries');
                if ~isempty(figs)
                    delete(figs);
                end
            catch
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

            % If integration is currently disabled during an active run, SI
            % cannot enable it live in 2023 API. Use frame fallback instead.
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

        function rg = buildIntegrationRoiGroup(obj, hSI, centers)
            rg = scanimage.mroi.RoiGroup('SlmSpiral Integration');
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

            roiDiameterUm = obj.getTraceRoiDiameterUm();
            roiSizeRef = roiDiameterUm ./ resXY;
            roiMask = obj.buildCircularMask(17);

            for i = 1:size(centers,1)
                centerRef = centers(i,:) ./ resXY;
                roi = scanimage.mroi.Roi();
                roi.name = sprintf('SpiralTrace C%d', i);
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

        function d = getTraceRoiDiameterUm(~)
            d = 10;
        end

        function m = buildCircularMask(~, nPix)
            if nargin < 2 || isempty(nPix)
                nPix = 17;
            end
            nPix = max(5, round(nPix));
            if ~mod(nPix,2)
                nPix = nPix + 1;
            end
            [xx,yy] = meshgrid(linspace(-1,1,nPix), linspace(-1,1,nPix));
            m = double((xx.^2 + yy.^2) <= 1);
        end

        function startFrameTraceUpdates(obj, hSI)
            obj.clearFrameTraceUpdates();
            if isempty(obj.hCh1Traces)
                return;
            end

            obj.lastTraceFrameNumber = -inf;
            try
                if most.idioms.isValidObj(hSI) && ~isempty(hSI.hDisplay) && most.idioms.isValidObj(hSI.hDisplay)
                    frameSeed = localScalarNumeric(hSI.hDisplay.lastFrameNumber, NaN);
                    if isfinite(frameSeed)
                        obj.lastTraceFrameNumber = frameSeed;
                    end
                end
            catch
                obj.lastTraceFrameNumber = -inf;
            end

            listenerOk = false;
            try
                if most.idioms.isValidObj(hSI) && ~isempty(hSI.hUserFunctions) ...
                        && most.idioms.isValidObj(hSI.hUserFunctions)
                    obj.hFrameTraceListener = addlistener(hSI.hUserFunctions, ...
                        'frameAcquired', @(~,~)obj.processMissingFramesForTraces(hSI));
                    listenerOk = true;
                end
            catch
                obj.hFrameTraceListener = [];
            end

            if ~listenerOk
                obj.hFrameTraceTimer = timer( ...
                    'Name','SlmSpiralFrameTrace', ...
                    'ExecutionMode','fixedSpacing', ...
                    'Period',0.02, ...
                    'TimerFcn',@(~,~)obj.processMissingFramesForTraces(hSI));
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
            obj.lastTraceFrameNumber = -inf;
        end

        function processMissingFramesForTraces(obj, hSI)
            if isempty(obj.hCh1Traces) || ~most.idioms.isValidObj(hSI) ...
                    || isempty(hSI.hDisplay) || ~most.idioms.isValidObj(hSI.hDisplay)
                return;
            end

            if obj.useIntegrationLogging
                try
                    hInt = hSI.hIntegrationRoiManager;
                    if most.idioms.isValidObj(hInt)
                        [~, values, ~, frameNumbers] = hInt.getIntegrationValues();
                        n = min(numel(values), numel(obj.hCh1Traces));
                        if n > 0
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
                            return;
                        end
                    end
                catch
                end
            end

            hDisp = hSI.hDisplay;
            if isempty(hDisp.lastStripeData) || isempty(hDisp.lastStripeData.roiData)
                return;
            end

            frameNumNow = localScalarNumeric(hDisp.lastFrameNumber, NaN);
            if ~isfinite(frameNumNow)
                return;
            end

            if isfinite(obj.lastTraceFrameNumber) && frameNumNow < obj.lastTraceFrameNumber
                obj.lastTraceFrameNumber = -inf;
            end

            frameNums = [];
            frameImgs = {};
            [imgLatest, frameLatest] = obj.getChannel1FrameFromStripe(hDisp.lastStripeData);
            if ~isempty(imgLatest) && ~isempty(frameLatest) && frameLatest > obj.lastTraceFrameNumber
                if ~isfinite(obj.lastTraceFrameNumber) || frameLatest <= obj.lastTraceFrameNumber + 1
                    frameNums = frameLatest;
                    frameImgs = {imgLatest};
                end
            end

            if isempty(frameNums)
                [frameNums, frameImgs] = obj.getMissingFramesFromBuffer(hDisp, obj.lastTraceFrameNumber, frameNumNow);
                if isempty(frameNums)
                    return;
                end
            end

            for iF = 1:numel(frameNums)
                frameNum = frameNums(iF);
                img = frameImgs{iF};
                for i = 1:numel(obj.hCh1Traces)
                    hTrace = obj.hCh1Traces{i};
                    if ~isempty(hTrace) && isvalid(hTrace)
                        hTrace.processFrame(frameNum, img, false);
                    end
                end
                obj.lastTraceFrameNumber = frameNum;
            end
        end

        function [frameNums, frameImgs] = getMissingFramesFromBuffer(~, hDisp, lastFrameNum, newestFrameNum)
            frameNums = [];
            frameImgs = {};
            try
                stripeBuf = hDisp.stripeDataBuffer;
            catch
                stripeBuf = {};
            end
            if isempty(stripeBuf) || ~iscell(stripeBuf)
                return;
            end

            n = numel(stripeBuf);
            tmpNums = nan(1,n);
            tmpImgs = cell(1,n);
            nValid = 0;
            for i = 1:n
                [img, frameNum] = getChannel1FrameFromStripeStatic(stripeBuf{i});
                if isempty(img) || isempty(frameNum)
                    continue;
                end
                nValid = nValid + 1;
                tmpNums(nValid) = frameNum;
                tmpImgs{nValid} = img;
            end
            if nValid < 1
                return;
            end

            tmpNums = tmpNums(1:nValid);
            tmpImgs = tmpImgs(1:nValid);
            [tmpNums, order] = sort(tmpNums);
            tmpImgs = tmpImgs(order);

            keepMask = [diff(tmpNums) ~= 0 true];
            tmpNums = tmpNums(keepMask);
            tmpImgs = tmpImgs(keepMask);

            pendingMask = tmpNums > lastFrameNum & tmpNums <= newestFrameNum;
            frameNums = tmpNums(pendingMask);
            frameImgs = tmpImgs(pendingMask);

            function [img, frameNum] = getChannel1FrameFromStripeStatic(stripe)
                img = [];
                frameNum = [];
                if isempty(stripe) || isempty(stripe.roiData)
                    return;
                end
                try
                    if isprop(stripe,'endOfFrame') && ~stripe.endOfFrame
                        return;
                    end
                catch
                end
                try
                    frameNum = stripe.frameNumberAcqMode;
                catch
                    frameNum = [];
                end
                if isempty(frameNum) || ~isfinite(frameNum)
                    return;
                end
                if ~isscalar(frameNum)
                    frameNum = frameNum(1);
                end

                rd = stripe.roiData{1};
                if isempty(rd.channels)
                    return;
                end

                chIdx = find(rd.channels == 1, 1, 'first');
                if isempty(chIdx)
                    return;
                end

                img = rd.imageData{chIdx};
                if iscell(img)
                    img = img{1};
                end
                if rd.transposed
                    img = img';
                end
            end
        end

        function [img, frameNum] = getChannel1FrameFromStripe(~, stripe)
            img = [];
            frameNum = [];
            if isempty(stripe) || isempty(stripe.roiData)
                return;
            end
            try
                if isprop(stripe,'endOfFrame') && ~stripe.endOfFrame
                    return;
                end
            catch
            end
            try
                frameNum = stripe.frameNumberAcqMode;
            catch
                frameNum = [];
            end
            if isempty(frameNum) || ~isfinite(frameNum)
                return;
            end
            if ~isscalar(frameNum)
                frameNum = frameNum(1);
            end

            rd = stripe.roiData{1};
            if isempty(rd.channels)
                return;
            end

            chIdx = find(rd.channels == 1, 1, 'first');
            if isempty(chIdx)
                return;
            end

            img = rd.imageData{chIdx};
            if iscell(img)
                img = img{1};
            end
            if rd.transposed
                img = img';
            end
        end

        function pickCentersFromImage(obj, varargin)
            hPickFig = [];
            try
                hSI = obj.resolveSI();
                [img, sf, chNum] = obj.getPickerImageAndScanfield(hSI);
                obj.cacheCellPickerRedImage(img, chNum);
                obj.saveCellPickerRedSnapshot(img, chNum);

                hPickFig = figure('Name',sprintf('Cell Picker (Channel %d)', chNum), ...
                    'NumberTitle','off', ...
                    'Color','w');
                ax = axes('Parent',hPickFig);
                imagesc(ax, img);
                axis(ax, 'image');
                set(ax, 'YDir', 'reverse');
                colormap(ax, gray(256));
                title(ax, 'Left click: add point | Enter/right-click: finish');
                xlabel(ax, 'X (pixels)');
                ylabel(ax, 'Y (pixels)');
                hold(ax, 'on');

                pickedPix = zeros(0,2);
                while true
                    [x, y, btn] = ginput(1);
                    if isempty(x) || isempty(y)
                        break;
                    end
                    if ~isempty(btn) && btn ~= 1
                        break;
                    end
                    pickedPix(end+1,:) = [x y]; %#ok<AGROW>
                    plot(ax, x, y, 'rx', 'LineWidth', 1.2, 'MarkerSize', 10);
                end

                if isempty(pickedPix)
                    obj.setStatus('Cell picker cancelled.');
                    return;
                end

                imgH = size(img,1);
                imgW = size(img,2);
                pickedPix(:,1) = min(max(pickedPix(:,1), 1), imgW);
                pickedPix(:,2) = min(max(pickedPix(:,2), 1), imgH);

                ptsRef = scanimage.mroi.util.xformPoints(pickedPix, sf.pixelToRefTransform());
                res = hSI.objectiveResolution;
                if isscalar(res)
                    centersUm = ptsRef * res;
                else
                    centersUm = ptsRef .* res(1:2);
                end

                obj.appendCenters(centersUm);
                obj.setStatus(sprintf('Imported %d center(s) from channel %d image.', size(centersUm,1), chNum));
            catch ME
                obj.setStatus(sprintf('Cell picker failed: %s', ME.message));
                most.ErrorHandler.logAndReportError(ME);
            end

            try
                if ~isempty(hPickFig) && isvalid(hPickFig)
                    delete(hPickFig);
                end
            catch
            end
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
            assert(~isempty(img) && isnumeric(img), 'Selected Channel 2 display image is empty.');

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
                try
                    rg = hSI.hRoiManager.currentRoiGroup;
                    assert(~isempty(rg) && ~isempty(rg.rois), 'No imaging ROI group available.');
                    sf = rg.rois(1).scanfields(1);
                catch
                    error('Could not resolve scanfield transform for displayed image.');
                end
            end

            pixRes = sf.pixelResolutionXY;
            imgSizeXY = [size(img,2) size(img,1)];
            assert(isequal(imgSizeXY, pixRes), ...
                ['Displayed image size does not match scanfield resolution. ' ...
                 'Ensure you are picking on the active imaging plane and try again.']);
        end

        function cacheCellPickerRedImage(obj, img, chNum)
            if isempty(img) || ~isnumeric(img)
                return;
            end
            obj.lastCellPickerRedImage = single(img);
            if nargin < 3 || isempty(chNum) || ~isfinite(chNum)
                obj.lastCellPickerRedChannel = NaN;
            else
                obj.lastCellPickerRedChannel = double(chNum);
            end
            obj.lastCellPickerRedTimestamp = datestr(now,'yyyy-mm-dd HH:MM:SS');
        end

        function savePath = saveCellPickerRedSnapshot(obj, img, chNum)
            savePath = '';
            if isempty(img) || ~isnumeric(img)
                return;
            end

            try
                name = obj.animal_name;
                if isstring(name)
                    name = char(name);
                end
                name = strtrim(name);
                if isempty(name)
                    name = 'cell_picker';
                end
                baseName = obj.sanitizeFilename(name);
                if isempty(baseName)
                    baseName = 'cell_picker';
                end

                if nargin < 3 || isempty(chNum) || ~isfinite(chNum)
                    chNum = NaN;
                end

                stamp = datestr(now,'yyyymmdd_HHMMSSFFF');
                if isfinite(chNum)
                    fName = sprintf('%s_cell_picker_red_ch%d_%s.mat', baseName, round(chNum), stamp);
                else
                    fName = sprintf('%s_cell_picker_red_%s.mat', baseName, stamp);
                end
                savePath = fullfile(pwd, fName);

                redImage = single(img); %#ok<NASGU>
                redChannel = chNum; %#ok<NASGU>
                redTimestamp = datestr(now,'yyyy-mm-dd HH:MM:SS'); %#ok<NASGU>
                save(savePath, 'redImage', 'redChannel', 'redTimestamp');
                obj.lastCellPickerRedPath = savePath;
            catch
                savePath = '';
            end
        end

        function [meanImg, meta] = computeTailMeanImageFromTiff(~, tiffPath, nTailFrames)
            meanImg = [];
            meta = struct('nFramesUsed',0, 'nFramesTotal',0, 'frameIndices',[]);

            if nargin < 3 || isempty(nTailFrames) || ~isfinite(nTailFrames) || nTailFrames < 1
                nTailFrames = 20;
            end
            nTailFrames = max(1, round(nTailFrames));

            if isempty(tiffPath)
                return;
            end
            if isstring(tiffPath)
                tiffPath = char(tiffPath);
            end
            if ~ischar(tiffPath) || ~exist(tiffPath, 'file')
                return;
            end

            % Avoid expensive metadata scans on very large TIFFs.
            try
                d = dir(tiffPath);
                if ~isempty(d)
                    maxBytesForTailMean = 1024^3; % 1 GiB
                    if d(1).bytes > maxBytesForTailMean
                        return;
                    end
                end
            catch
            end

            try
                info = imfinfo(tiffPath);
                nFramesTotal = numel(info);
                meta.nFramesTotal = nFramesTotal;
                if nFramesTotal < 1
                    return;
                end

                nUse = min(nTailFrames, nFramesTotal);
                idxStart = nFramesTotal - nUse + 1;
                frameIdx = idxStart:nFramesTotal;
                meta.frameIndices = frameIdx;

                acc = [];
                nAccum = 0;
                for i = 1:numel(frameIdx)
                    frm = imread(tiffPath, frameIdx(i), 'Info', info);
                    frm = double(frm);

                    if isempty(acc)
                        acc = zeros(size(frm), 'double');
                    elseif ~isequal(size(frm), size(acc))
                        continue;
                    end

                    acc = acc + frm;
                    nAccum = nAccum + 1;
                end

                if nAccum > 0
                    meanImg = single(acc ./ nAccum);
                    meta.nFramesUsed = nAccum;
                else
                    meta.frameIndices = [];
                end
            catch
                meanImg = [];
                meta = struct('nFramesUsed',0, 'nFramesTotal',0, 'frameIndices',[]);
            end
        end

        function appendCenters(obj, centersUm)
            if isempty(centersUm)
                return;
            end

            existingCenters = zeros(0,2);
            useExisting = true;
            try
                existingCenters = obj.getCenterList();
                if size(existingCenters,1) == 1 && all(abs(existingCenters(1,:)) < 1e-9)
                    useExisting = false;
                end
            catch
                useExisting = false;
            end

            if useExisting
                centersAll = [existingCenters; centersUm];
            else
                centersAll = centersUm;
            end
            obj.centerX_list = obj.formatNumberList(centersAll(:,1));
            obj.centerY_list = obj.formatNumberList(centersAll(:,2));
        end

        function s = formatNumberList(~, vals)
            if isempty(vals)
                s = '';
                return;
            end
            s = strjoin(arrayfun(@(v)sprintf('%.3f', v), vals(:).', 'UniformOutput', false), ',');
        end

        function runPsth(obj, varargin)
            try
                defaultPick = fullfile(pwd, '*.mat');
                if ~isempty(obj.lastSavePath) && exist(obj.lastSavePath, 'file')
                    defaultPick = obj.lastSavePath;
                end

                [f, p] = uigetfile({'*.mat','stimData .mat files (*.mat)'}, ...
                    'Select stimData .mat file', defaultPick);
                if isequal(f,0)
                    obj.setStatus('PSTH cancelled.');
                    return;
                end
                filePath = fullfile(p, f);
                obj.lastSavePath = filePath;
                scanimage.guis.SlmSpiralPsthAnalysis(filePath);
                obj.setStatus(sprintf('PSTH loaded: %s', filePath));
            catch ME
                obj.setStatus(sprintf('PSTH failed: %s', ME.message));
            end
        end

        function [triggerPlan, idxTriggerList, nCenters] = buildTriggerPlan(obj, groupInfo, seqReps)
            entryTemplate = struct('rep',[],'combo_index',[],'linear_combo_index',[], ...
                'power_index',[],'duration_index',[],'center_index',[], ...
                'center_x_um',[],'center_y_um',[],'group_index',[], ...
                'power_pct',[],'duration_ms',[]);
            triggerPlan = repmat(entryTemplate, 0, 1);
            idxTriggerList = zeros(1,0);

            if isempty(groupInfo)
                nCenters = 0;
                return;
            end

            nCenters = numel(unique([groupInfo.center_index], 'stable'));
            nInfo = numel(groupInfo);
            totalEntries = nInfo * seqReps;
            triggerPlan = repmat(entryTemplate, totalEntries, 1);
            idxTriggerList = zeros(1, totalEntries);
            writeIdx = 0;

            for iRep = 1:seqReps
                order = obj.buildCenterRandomOrder(groupInfo);
                if isempty(order)
                    order = 1:nInfo;
                end

                for iE = 1:numel(order)
                    e = groupInfo(order(iE));
                    p = entryTemplate;
                    p.rep = iRep;
                    p.combo_index = e.combo_index;
                    p.linear_combo_index = e.linear_combo_index;
                    p.power_index = e.power_index;
                    p.duration_index = e.duration_index;
                    p.center_index = e.center_index;
                    p.center_x_um = e.center_x_um;
                    p.center_y_um = e.center_y_um;
                    p.group_index = e.group_index;
                    p.power_pct = e.power_pct;
                    p.duration_ms = e.duration_ms;
                    writeIdx = writeIdx + 1;
                    triggerPlan(writeIdx,1) = p;
                    idxTriggerList(writeIdx) = e.group_index;
                end
            end

            if writeIdx < totalEntries
                triggerPlan = triggerPlan(1:writeIdx,1);
                idxTriggerList = idxTriggerList(1:writeIdx);
            end
        end

        function order = buildCenterRandomOrder(~, groupInfo)
            order = [];
            nInfo = numel(groupInfo);
            if nInfo <= 1
                order = 1:nInfo;
                return;
            end

            centers = [groupInfo.center_index];
            uCenters = unique(centers, 'stable');
            nCenters = numel(uCenters);
            if nCenters <= 1
                order = randperm(nInfo);
                return;
            end

            idxByCenter = cell(nCenters, 1);
            for i = 1:nCenters
                idxByCenter{i} = find(centers == uCenters(i));
            end

            counts = cellfun(@numel, idxByCenter);
            if numel(unique(counts)) ~= 1
                order = randperm(nInfo);
                return;
            end
            nPerCenter = counts(1);

            maxTries = 50;
            for attempt = 1:maxTries
                for i = 1:nCenters
                    idxByCenter{i} = idxByCenter{i}(randperm(nPerCenter));
                end

                order = zeros(1, nInfo);
                pos = 1;
                lastCenter = NaN;
                for r = 1:nPerCenter
                    centerOrder = randperm(nCenters);
                    if ~isnan(lastCenter) && uCenters(centerOrder(1)) == lastCenter
                        swapIdx = find(uCenters(centerOrder) ~= lastCenter, 1, 'first');
                        if ~isempty(swapIdx)
                            tmp = centerOrder(1);
                            centerOrder(1) = centerOrder(swapIdx);
                            centerOrder(swapIdx) = tmp;
                        end
                    end

                    for j = 1:numel(centerOrder)
                        cIdx = centerOrder(j);
                        order(pos) = idxByCenter{cIdx}(r);
                        pos = pos + 1;
                        lastCenter = uCenters(cIdx);
                    end
                end

                if numel(order) == nInfo
                    cseq = centers(order);
                    if all(diff(cseq) ~= 0) && cseq(1) ~= cseq(end)
                        return;
                    end
                end
            end

            order = randperm(nInfo);
        end

        function stopImagingIfStarted(obj, startedImaging, hSI)
            if startedImaging
                try
                    if most.idioms.isValidObj(hSI) && obj.isSiActiveSafe(hSI)
                        hSI.abort();
                    end
                catch
                end
            end
        end

        function wait_s = waitForNextFrameEdge(obj, hSI, timeout_s)
            wait_s = 0;
            if nargin < 3 || isempty(timeout_s) || ~isfinite(timeout_s) || timeout_s <= 0
                timeout_s = 2;
            end
            if ~most.idioms.isValidObj(hSI) || ~obj.isSiActiveSafe(hSI) ...
                    || isempty(hSI.hDisplay) || ~most.idioms.isValidObj(hSI.hDisplay)
                return;
            end

            frame0 = [];
            try
                frame0 = localScalarNumeric(hSI.hDisplay.lastFrameNumber, NaN);
            catch
                return;
            end
            if ~isfinite(frame0)
                frame0 = -inf;
            end

            tStart = tic;
            while toc(tStart) < timeout_s
                pause(0.001);
                if ~most.idioms.isValidObj(hSI) || ~obj.isSiActiveSafe(hSI)
                    break;
                end
                try
                    frameNow = localScalarNumeric(hSI.hDisplay.lastFrameNumber, NaN);
                catch
                    break;
                end
                if isfinite(frameNow) && frameNow ~= frame0
                    wait_s = toc(tStart);
                    return;
                end
            end
            wait_s = toc(tStart);
        end

        function restoreStimTriggerTermSafe(obj, hPhotostim, trigTerm)
            try
                if most.idioms.isValidObj(hPhotostim) && ~obj.isPhotostimActiveSafe(hPhotostim)
                    hPhotostim.stimTriggerTerm = trigTerm;
                end
            catch
            end
        end

        function tf = getPhotostimMonitoringSafe(~, hPhotostim)
            tf = false;
            try
                if most.idioms.isValidObj(hPhotostim) && isprop(hPhotostim,'monitoring')
                    tf = localToLogical(hPhotostim.monitoring);
                end
            catch
                tf = false;
            end
        end

        function changed = setPhotostimMonitoringSafe(obj, hPhotostim, val)
            changed = false;
            try
                if ~most.idioms.isValidObj(hPhotostim)
                    return;
                end

                v = localToLogical(val);
                oldVal = obj.getPhotostimMonitoringSafe(hPhotostim);
                if oldVal == v
                    return;
                end

                hPhotostim.monitoring = v;
                changed = obj.getPhotostimMonitoringSafe(hPhotostim) == v;
            catch
                changed = false;
            end
        end

        function restorePhotostimMonitoringSafe(obj, hPhotostim, oldVal, changed)
            if nargin < 4 || ~changed
                return;
            end
            try
                if ~most.idioms.isValidObj(hPhotostim)
                    return;
                end
                v = localToLogical(oldVal);
                if obj.getPhotostimMonitoringSafe(hPhotostim) ~= v
                    hPhotostim.monitoring = v;
                end
            catch
            end
        end

        function tf = getPhotostimLoggingSafe(~, hPhotostim)
            tf = false;
            try
                if most.idioms.isValidObj(hPhotostim) && isprop(hPhotostim,'logging')
                    tf = localToLogical(hPhotostim.logging);
                end
            catch
                tf = false;
            end
        end

        function changed = setPhotostimLoggingSafe(obj, hPhotostim, val)
            changed = false;
            try
                if ~most.idioms.isValidObj(hPhotostim) || obj.isPhotostimActiveSafe(hPhotostim)
                    return;
                end

                v = localToLogical(val);
                oldVal = obj.getPhotostimLoggingSafe(hPhotostim);
                if oldVal == v
                    return;
                end

                hPhotostim.logging = v;
                changed = obj.getPhotostimLoggingSafe(hPhotostim) == v;
            catch
                changed = false;
            end
        end

        function [ok,msg] = restorePhotostimLoggingSafe(obj, hPhotostim, oldVal, changed)
            ok = true;
            msg = '';
            if nargin < 4 || ~changed
                return;
            end
            ok = false;
            msg = 'Photostim logging restore failed.';
            try
                if ~most.idioms.isValidObj(hPhotostim) || obj.isPhotostimActiveSafe(hPhotostim)
                    return;
                end
                v = localToLogical(oldVal);
                if obj.getPhotostimLoggingSafe(hPhotostim) ~= v
                    hPhotostim.logging = v;
                end
                if obj.getPhotostimLoggingSafe(hPhotostim) == v
                    ok = true;
                    msg = 'Photostim logging restored.';
                end
            catch
            end
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
                    error(['Could not enable photostim logging. If imaging is already active, ' ...
                        'stop imaging and run again. Details: %s'], ME.message);
                end
            end

            try
                if isprop(hPhotostim,'pairStimActiveOutputChannel') && ~hPhotostim.pairStimActiveOutputChannel
                    most.idioms.warn(['Photostim pairStimActiveOutputChannel is disabled. ' ...
                        'Stim timestamps may not be routed to imaging Aux Trigger 3.']);
                end
            catch
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
                % Best-effort UI sync only; hard checks happen in stimulateNow().
            end
        end

        function startAutoAbortTimer(obj, delay_s, hPhotostim)
            obj.clearAutoAbortTimer();
            if isempty(delay_s) || ~isfinite(delay_s) || delay_s <= 0
                return;
            end

            obj.hAutoAbortTimer = timer( ...
                'Name','SlmSpiralAutoAbort', ...
                'ExecutionMode','singleShot', ...
                'StartDelay',delay_s, ...
                'TimerFcn',@(~,~)doAbort());
            start(obj.hAutoAbortTimer);

            function doAbort()
                try
                    if most.idioms.isValidObj(hPhotostim)
                        hPhotostim.abort();
                    end
                catch
                end
                obj.clearAutoAbortTimer();
            end
        end

        function clearAutoAbortTimer(obj)
            try
                if ~isempty(obj.hAutoAbortTimer) && isvalid(obj.hAutoAbortTimer)
                    stop(obj.hAutoAbortTimer);
                    delete(obj.hAutoAbortTimer);
                end
            catch
            end
            obj.hAutoAbortTimer = [];
        end

        function [elapsed_s, aborted] = pauseWithAbort(obj, wait_s, hPhotostim)
            elapsed_s = 0;
            aborted = false;
            if isempty(wait_s) || ~isfinite(wait_s) || wait_s <= 0
                return;
            end

            checkPhotostim = nargin >= 3 && most.idioms.isValidObj(hPhotostim);
            tStart = tic;
            while elapsed_s < wait_s
                if obj.abortRequested
                    aborted = true;
                    try
                        if checkPhotostim && obj.isPhotostimActiveSafe(hPhotostim)
                            hPhotostim.abort();
                        end
                    catch
                    end
                    break;
                end

                dt = min(0.05, wait_s - elapsed_s);
                pause(dt);
                drawnow limitrate;

                if checkPhotostim && ~obj.isPhotostimActiveSafe(hPhotostim)
                    aborted = true;
                    break;
                end
                elapsed_s = toc(tStart);
            end
            elapsed_s = min(wait_s, toc(tStart));
        end

        function clearRunState(obj)
            obj.abortRequested = false;
            obj.runInProgress = false;
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

        function setStatus(obj,msg)
            if isempty(obj.etStatus) || ~most.idioms.isValidObj(obj.etStatus)
                return;
            end
            obj.etStatus.String = msg;
        end

        function centers = getCenterList(obj)
            xs = obj.parseNumberList(obj.centerX_list, 'Centers X');
            ys = obj.parseNumberList(obj.centerY_list, 'Centers Y');

            if isempty(xs) || isempty(ys)
                error('Centers X and Centers Y must both be provided.');
            end
            if numel(xs) ~= numel(ys)
                error('Centers X and Centers Y must have the same number of values.');
            end
            centers = [xs(:) ys(:)];
            if isempty(centers)
                error('No valid centers provided.');
            end
        end

        function vals = parseNumberList(~, spec, name)
            if nargin < 3
                name = 'Values';
            end

            if isnumeric(spec)
                vals = spec(:).';
                if isempty(vals)
                    vals = [];
                end
                return;
            end

            if isstring(spec)
                spec = char(spec);
            end
            spec = strtrim(spec);
            if isempty(spec)
                vals = [];
                return;
            end

            spec = regexprep(spec, '[\[\]\(\)\{\}]', '');
            spec = strrep(spec, ';', ',');
            spec = regexprep(spec, '\s+', ',');
            parts = strsplit(spec, ',');
            parts = parts(~cellfun('isempty', parts));
            if isempty(parts)
                vals = [];
                return;
            end

            vals = nan(1, numel(parts));
            for i = 1:numel(parts)
                vals(i) = str2double(parts{i});
            end
            if any(~isfinite(vals))
                error('Invalid %s list: %s', name, spec);
            end
        end

        function updateEstimate(obj)
            if isempty(obj.etEstimate) || ~most.idioms.isValidObj(obj.etEstimate)
                return;
            end
            try
                centers = obj.getCenterList();
                [powerVals, durationVals] = obj.getPowerDurationVals();
                validateattributes(obj.sequence_repetitions,{'numeric'},{'scalar','finite','real','nonnan','>=',1});
                seqReps = round(obj.sequence_repetitions);
                validateattributes(obj.interStimGap_ms,{'numeric'},{'scalar','finite','real','nonnan','>=',0});
                validateattributes(obj.preStimDelay_ms,{'numeric'},{'scalar','finite','real','nonnan','>=',0});
                validateattributes(obj.postStimDelay_ms,{'numeric'},{'scalar','finite','real','nonnan','>=',0});

                nP = numel(powerVals);
                nD = numel(durationVals);
                nC = size(centers,1);
                nCombos = nP * nD * nC;
                totalTriggers = nCombos * seqReps;

                total_ms = obj.preStimDelay_ms + obj.postStimDelay_ms;
                if totalTriggers > 0
                    durationsRep = repmat(durationVals, 1, nP * nC);
                    perTrigger_ms = obj.prePause_ms + durationsRep + obj.postPark_ms;
                    total_ms = total_ms + seqReps * sum(perTrigger_ms);
                    if totalTriggers > 1
                        total_ms = total_ms + (totalTriggers - 1) * obj.interStimGap_ms;
                    end
                end

                total_s = total_ms / 1000;
                if total_s >= 60
                    mins = floor(total_s / 60);
                    secs = total_s - mins * 60;
                    msg = sprintf('%dm %.1fs', mins, secs);
                else
                    msg = sprintf('%.1fs', total_s);
                end
                obj.etEstimate.String = msg;
            catch
                obj.etEstimate.String = 'n/a';
            end
        end

        function name = sanitizeFilename(~, name)
            name = regexprep(name, '[<>:"/\\|?*]', '_');
            name = regexprep(name, '\s+', '_');
            name = regexprep(name, '\.+$', '');
            name = strtrim(name);
        end

        function [powerVals, durationVals] = getPowerDurationVals(obj)
            powerVals = obj.buildRange(obj.power_start_pct, obj.power_stop_pct, 'Power (%)');
            durationVals = obj.buildRange(obj.duration_start_ms, obj.duration_stop_ms, 'Duration (ms)');

            if any(powerVals < 0) || any(powerVals > 100)
                error('Power must be between 0 and 100.');
            end
            if any(durationVals <= 0)
                error('Duration must be > 0.');
            end
        end

        function vals = buildRange(obj, startVal, stopVal, name)
            if nargin < 4
                name = 'Value';
            end

            validateattributes(startVal,{'numeric'},{'scalar','finite','real','nonnan'});
            if isempty(stopVal)
                stopVal = startVal;
            else
                validateattributes(stopVal,{'numeric'},{'scalar','finite','real','nonnan'});
            end

            validateattributes(obj.rangeStep,{'numeric'},{'scalar','finite','real','nonnan','>',0});
            stepVal = obj.rangeStep;
            if startVal <= stopVal
                vals = startVal:stepVal:stopVal;
            else
                vals = startVal:-stepVal:stopVal;
            end

            vals = vals(:).';
            if isempty(vals)
                error('%s range produced no values.', name);
            end
        end
    end
end

function tf = localToLogical(v)
% Convert values commonly returned by SI/Photostim "active"-style
% properties into a safe logical scalar.

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
% Return one finite scalar numeric value from x, or defaultValue.

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
