function siListener()
% generic code for listening for UDPs
global netObj;
global listenerStatus;
%% setup UDP listener...
nn = NET.addAssembly('C:\Code\UDPWithEvents\UDPWithEvents.dll');
listenerStatus.listenport = 1813;
listenerStatus.sendport   = 1814;
listenerStatus.local = 'F:\Local_Repository';
listenerStatus.remote = '\\ar-lab-nas1\DataServer\Remote_Repository';
% Create the object similar to an UdpClient
netObj = UDPWithEvents.UDPWithEvents(listenerStatus.listenport);

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
listenerStatus.udpObject = udp('158.109.210.77',listenerStatus.sendport);
fopen(listenerStatus.udpObject);

% Setup default status of listener
listenerStatus.expID = '2014-01-01_01_TEST';

% Start listening
netObj.BeginReceive;
disp('Listening');

end


function UDP_handler(src,datain)

% parse UDP message
% take action depending upon first 4 letters of UDP

global hSI;
global hSICtl;
global listenerStatus;
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
                if ~strcmp(hSI.acqState,'idle')
                    disp('Already acquiring so aborting');
                    % if SI not idle then make idle
                    hSI.abort;
                    % wait for idle for max 10 secs
                    startAbort = tic;
                    while ~strcmp(hSI.acqState,'idle')&&toc(startAbort)<10
                        drawnow();
                    end
                    if ~strcmp(hSI.acqState,'idle');msgbox('Timed out waiting for SI to be ready');return;end;
                end
                
                listenerStatus.expID = udpData.meta{1};
                listenerStatus.animalID = listenerStatus.expID(15:end);
                disp(['Exp ID: ',udpData.meta{1}]);
                % ensure directory exists locally
                
                expDir = fullfile(listenerStatus.local, listenerStatus.animalID, listenerStatus.expID, 'P1');
                if ~exist(expDir,'dir')
                    mkdir(expDir)
                end
                
                expDirRemote = fullfile(listenerStatus.remote, listenerStatus.animalID, listenerStatus.expID, 'P1');

                if ~exist(expDirRemote,'dir')
                    mkdir(expDirRemote)
                end
                
                hSI.hScan2D.logFilePath = expDir;
                hSI.hScan2D.logFileStem = [listenerStatus.expID,'_2P'];
                
                
                % save meta data about the acquisition such as roi
                selectedScanfieldMetaPath = '';
                try
                    [~, selectedScanfieldMetaPath] = saveSelectedScanfieldToFolder(hSI, hSICtl, expDir, listenerStatus.expID);
                    if ~isempty(selectedScanfieldMetaPath)
                        disp(['Saved selected scanfield metadata to ', selectedScanfieldMetaPath]);
                    end
                catch ME
                    warning('siListener:SelectedScanfieldSaveFailed', ...
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
                    metaPath = fullfile(expDir,[listenerStatus.expID,'_imageMeta.mat']);
                    if ~exist(expDir,'dir')
                        mkdir(expDir)
                    end
                    save(metaPath,'imagingMeta');
                end
                
                % start the acquisition and wait for confirmation
                %evalin('base','hSI.startGrab');
                %T = timer('StartDelay',1,'TimerFcn',@(src,evt)evalin('base','hSI.startGrab'));
                % start(T)
                hSI.hChannels.loggingEnable=true;
                hSI.startGrab;
                % drawnow;
                disp('Requested start grabbing');
                startGrab = tic;
                while ~strcmp(hSI.acqState,'grab')&&(toc(startGrab)<10)
                    drawnow();
                end
                
                disp('Grabbing confirmed');
                % send ready command
                messageStruct.messageData = 'READY';
                messageStruct.messageType = 'COM';
                messageStruct.confirmID = round(rand*10^6);
                messageStruct.confirm = 0;
                messageStructSerial = hlp_serialize(messageStruct);
%                 fclose(listenerStatus.udpObject);
%                 fopen(listenerStatus.udpObject);
                fwrite(listenerStatus.udpObject,messageStructSerial);
%                 fclose(listenerStatus.udpObject);
                src.BeginReceive;
                disp('Grabbing confirmed');
    
            case 'STOP'
                hSI.abort;
                disp('=======');
                disp('Received STOP signal');
                startAbort = tic;
                while ~strcmp(hSI.acqState,'idle')&&(toc(startAbort)<10)
                    drawnow();
                end
                if ~strcmp(hSI.acqState,'idle');msgbox('Timed out waiting for SI to be ready');
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
%                 fclose(listenerStatus.udpObject);
                %listenerStatus.udpObject = udp('158.109.215.50',listenerStatus.sendport);
%                 close_attempts = 0;
%                 close_timeout = tic;
%                 fopen(listenerStatus.udpObject);
%                 while strcmp(listenerStatus.udpObject.Status,'open')
%                      fclose(listenerStatus.udpObject);
%                     close_attempts = close_attempts + 1;
%                     if toc(close_timeout)>10
%                         disp('Timed out trying to close UDP connection');
%                         break;
%                     end
%                 end
%                 disp(['Time to close connection = ',num2str(toc(close_timeout)),' secs']);
%                 disp(['Close attempts           = ',num2str(close_attempts),' secs']);
%                 disp('fclose(listenerStatus.udpObject); OK');
%                 fopen(listenerStatus.udpObject);
                disp('fopen(listenerStatus.udpObject); OK');
                fwrite(listenerStatus.udpObject,messageStructSerial);
                disp('fwrite(listenerStatus.udpObject,messageStructSerial); OK');
%                 fclose(listenerStatus.udpObject);
                disp('fclose(listenerStatus.udpObject); OK');
                src.BeginReceive;
                disp('src.BeginReceive; OK');
        end
    end
    
    
end
