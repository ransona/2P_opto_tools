function siListener2()
% generic code for listening for UDPs
global netObj;
global listenerStatus2;
%% setup UDP listener...
nn = NET.addAssembly('C:\Code\UDPWithEvents\UDPWithEvents.dll');
listenerStatus2.listenport = 1821;
listenerStatus2.sendport   = 1822;
listenerStatus2.local = 'F:\Local_Repository';
listenerStatus2.remote = '\\ar-lab-nas1\DataServer\Remote_Repository';
% Create the object similar to an UdpClient
netObj = UDPWithEvents.UDPWithEvents(listenerStatus2.listenport);

% Subscribe to the OnReceive event. When the event is raised the myhandler
% function is called
addlistener(netObj,'OnReceive',@UDP_handler);

% delete all existing udp objects
all_udp = instrfindall('Type','udp');
if ~isempty(all_udp)
    fclose(all_udp);
    delete(all_udp);
end
% make the udp to send
listenerStatus2.udpObject = udp('158.109.210.77',listenerStatus2.sendport);
fopen(listenerStatus2.udpObject);

% Setup default status of listener
listenerStatus2.expID = '2014-01-01_01_TEST';

% Start listening
netObj.BeginReceive;
disp('Listening');

end


function UDP_handler(src,datain)

% parse UDP message
% take action depending upon first 4 letters of UDP

global hSI2;
global hSICtl2;
global listenerStatus2;
global siMotorData;
debugOn = false;

UDP_Received = char(int32(datain));
%disp(UDP_Received);
udpData = hlp_deserialize(UDP_Received);

if strcmp(udpData.messageType,'COM')
    % if it's a command
    switch udpData.messageData
        case 'GOGO'
            % check space on disk is > 50GB
            FileObj      = java.io.File('F:\');
            free_gb   = FileObj.getFreeSpace*1e-9;
            if free_gb < 200
                switch questdlg(['Space free = ',num2str(round(free_gb)),'GB - suggest delete data before continuing. Would you like to do this?'])
                    case 'Yes'
                        return
                end
            end
            disp('=======');
            disp('Received GOGO signal');
            if ~strcmp(hSI2.acqState,'idle')
                disp('Already acquiring so aborting');
                % if SI not idle then make idle
                hSI2.abort;
                % wait for idle for max 10 secs
                startAbort = tic;
                while ~strcmp(hSI2.acqState,'idle')&&toc(startAbort)<10
                    drawnow();
                end
                if ~strcmp(hSI2.acqState,'idle');msgbox('Timed out waiting for SI to be ready');return;end;
            end

            listenerStatus2.expID = udpData.meta{1};
            listenerStatus2.animalID = listenerStatus2.expID(15:end);
            disp(['Exp ID: ',udpData.meta{1}]);
            % ensure directory exists locally

            expDir = fullfile(listenerStatus2.local, listenerStatus2.animalID, listenerStatus2.expID, 'P2');
            if ~exist(expDir,'dir')
                mkdir(expDir)
            end

            expDirRemote = fullfile(listenerStatus2.remote, listenerStatus2.animalID, listenerStatus2.expID, 'P2');
            if ~exist(expDirRemote,'dir')
                mkdir(expDirRemote)
            end

            hSI2.hScan2D.logFilePath = expDir;
            hSI2.hScan2D.logFileStem = [listenerStatus2.expID,'_2P'];


            % save meta data about the acquisition such as roi
            selectedScanfieldMetaPath = '';
            try
                [~, selectedScanfieldMetaPath] = saveSelectedScanfieldToFolder(hSI2, hSICtl2, expDir, listenerStatus2.expID);
                if ~isempty(selectedScanfieldMetaPath)
                    disp(['Saved selected scanfield metadata to ', selectedScanfieldMetaPath]);
                end
            catch ME
                warning('siListener2:SelectedScanfieldSaveFailed', ...
                    'Failed to save selected scanfield metadata: %s', ME.message);
            end

            imagingMeta = struct();
            if ~isempty(siMotorData) && isfield(siMotorData,'currentRoi')
                imagingMeta.currentRoi = siMotorData.currentRoi;
            end
            if ~isempty(selectedScanfieldMetaPath)
                [~, scanfieldMetaName, scanfieldMetaExt] = fileparts(selectedScanfieldMetaPath);
                imagingMeta.selectedScanfieldFile = [scanfieldMetaName, scanfieldMetaExt];
            end
            if ~isempty(fieldnames(imagingMeta))
                metaPath = fullfile(expDir,[listenerStatus2.expID,'_imageMeta.mat']);
                if ~exist(expDir,'dir')
                    mkdir(expDir)
                end
                save(metaPath,'imagingMeta');
            end

            % start the acquisition and wait for confirmation
            %evalin('base','hSI2.startGrab');
            %T = timer('StartDelay',1,'TimerFcn',@(src,evt)evalin('base','hSI2.startGrab'));
            % start(T)
            hSI2.hChannels.loggingEnable=true;
            hSI2.startGrab;
            % drawnow;
            disp('Requested start grabbing');
            startGrab = tic;
            while ~strcmp(hSI2.acqState,'grab')&&(toc(startGrab)<10)
                drawnow();
            end

            disp('Grabbing confirmed');
            % send ready command
            messageStruct.messageData = 'READY';
            messageStruct.messageType = 'COM';
            messageStruct.confirmID = round(rand*10^6);
            messageStruct.confirm = 0;
            messageStructSerial = hlp_serialize(messageStruct);
            %                 fclose(listenerStatus2.udpObject);
            %                 fopen(listenerStatus2.udpObject);
            fwrite(listenerStatus2.udpObject,messageStructSerial);
            %                 fclose(listenerStatus2.udpObject);
            src.BeginReceive;
            disp('Grabbing confirmed');

        case 'STOP'
            hSI2.abort;
            disp('=======');
            disp('Received STOP signal');
            startAbort = tic;
            while ~strcmp(hSI2.acqState,'idle')&&(toc(startAbort)<10)
                drawnow();
            end
            if ~strcmp(hSI2.acqState,'idle');msgbox('Timed out waiting for SI to be ready');
                return;
            end
            disp('Stopped');
            % send ready command
            messageStruct.messageData = 'READY';
            messageStruct.messageType = 'COM';
            messageStruct.confirmID = round(rand*10^6);
            messageStruct.confirm = 0;
            messageStructSerial = hlp_serialize(messageStruct);
            disp(' messageStructSerial = hlp_serialize(messageStruct); OK');
            %                 fclose(listenerStatus2.udpObject);
            %listenerStatus2.udpObject = udp('158.109.215.50',listenerStatus2.sendport);
            %                 close_attempts = 0;
            %                 close_timeout = tic;
            %                 fopen(listenerStatus2.udpObject);
            %                 while strcmp(listenerStatus2.udpObject.Status,'open')
            %                      fclose(listenerStatus2.udpObject);
            %                     close_attempts = close_attempts + 1;
            %                     if toc(close_timeout)>10
            %                         disp('Timed out trying to close UDP connection');
            %                         break;
            %                     end
            %                 end
            %                 disp(['Time to close connection = ',num2str(toc(close_timeout)),' secs']);
            %                 disp(['Close attempts           = ',num2str(close_attempts),' secs']);
            %                 disp('fclose(listenerStatus2.udpObject); OK');
            %                 fopen(listenerStatus2.udpObject);
            disp('fopen(listenerStatus2.udpObject); OK');
            fwrite(listenerStatus2.udpObject,messageStructSerial);
            disp('fwrite(listenerStatus2.udpObject,messageStructSerial); OK');
            %                 fclose(listenerStatus2.udpObject);
            disp('fclose(listenerStatus2.udpObject); OK');
            src.BeginReceive;
            disp('src.BeginReceive; OK');
    end
end


end
