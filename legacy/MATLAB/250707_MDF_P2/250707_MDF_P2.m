% Most Software Machine Data File

%% scanimage.SI (ScanImage)

% Global microscope properties
objectiveResolution = 73.4096;     % Resolution of the objective in microns/degree of scan angle

% Data file location

% Custom Scripts
startUpScript = '';     % Name of script that is executed in workspace 'base' after scanimage initializes
shutDownScript = '';     % Name of script that is executed in workspace 'base' after scanimage exits

fieldCurvatureZs = [];     % Field curvature for mesoscope
fieldCurvatureRxs = [];     % Field curvature for mesoscope
fieldCurvatureRys = [];     % Field curvature for mesoscope
fieldCurvatureTip = 0;     % Field tip for mesoscope
fieldCurvatureTilt = 0;     % Field tilt for mesoscope
useJsonHeaderFormat = false;     % Use JSON format for TIFF file header

minimizeOnStart = false;
widgetVisibility = true;

%% scanimage.components.CoordinateSystems (SI CoordinateSystems)
% SI Coordinate System Component.
classDataFileName = 'default-CoordinateSystems_classData.mat';     % File containing the previously generated alignment data corresponding to the currently installed objective, SLM, scanners, etc.

%% scanimage.components.Motors (SI Motors)
% SI Stage/Motor Component.
motorXYZ = {'Zaber Motor' 'Zaber Motor' 'Zaber Motor'};     % Defines the motor for ScanImage axes X Y Z.
motorAxisXYZ = [1 2 3];     % Defines the motor axis used for Scanimage axes X Y Z.
scaleXYZ = [1 1 1];     % Defines scaling factors for axes.
backlashCompensation = [0 0 0];     % Backlash compensation in um (positive or negative)
moveTimeout_s = 10;     % Move timeout in seconds

%% scanimage.components.Photostim (SI Photostim)
photostimScannerName = '';     % Name of scanner (from first MDF section) to use for photostimulation. Must be a linear scanner
stimTriggerTerm = '';     % Specifies the channel that should be used to trigger a stimulation. This a triggering port name such as D2.1 for vDAQ or PFI1 for the auxiliary IO board of an NI LinScan system.

% Monitoring DAQ AI channels
BeamAiId = '';     % AI channel to be used for monitoring the Pockels cell output

loggingStartTrigger = '';     % PFI line to which start trigger for logging is wired to photostim board. Leave empty for automatic routing via PXI bus

stimActiveOutputChannel = '';     % Digital terminal on stim board to output stim active signal. (e.g. on vDAQ: 'D2.6' on NI-DAQ hardware: '/port0/line0'
beamActiveOutputChannel = '';     % Digital terminal on stim board to output beam active signal. (e.g. on vDAQ: 'D2.7' on NI-DAQ hardware: '/port0/line1'
slmTriggerOutputChannel = '';     % Digital terminal on stim board to trigger SLM frame flip. (e.g. on vDAQ: 'D2.5' on NI-DAQ hardware: '/port0/line2'

%% dabs.generic.ResonantScannerAnalog (X Reso P2)
AOZoom = '/Dev1/AO1';     % zoom control terminal  e.g. '/vDAQ0/AO0'
DOEnable = '/vDAQ0/D1.1';     % digital enable terminal e.g. '/vDAQ0/D0.1'
DISync = '/vDAQ0/D1.0';     % digital sync terminal e.g. '/vDAQ0/D0.0'

nominalFrequency = 7910;     % nominal resonant frequency in Hz
angularRange = 24;     % total angular range in optical degrees (e.g. for a resonant scanner with -13..+13 optical degrees, enter 26)
voltsPerOpticalDegrees = 0.1923;     % volts per optical degrees for the control signal
settleTime = 0.5;     % settle time in seconds to allow the resonant scanner to turn on

% Calibration Settings
amplitudeToLinePhaseMap = [0.393 1.39417e-06;1.333 -5.06971e-08;2.6 -2.816e-07;3 -4.352e-07;3.429 -3.584e-07;4 -4.48e-07;4.8 -4.30925e-07;6 -4.608e-07;8 -5.504e-07;8.667 -5.504e-07;14.712 -5.888e-07;17.719 -6.21039e-07;24 -6.528e-07;26 -6.144e-07];     % translates an amplitude (degrees) to a line phase (seconds)
amplitudeToFrequencyMap = [1.333 7920.57;2.6 7918.94;2.615 7922.26;3 7919.64;3.429 7919.94;3.433 7921.74;4 7918.97;4.496 7923.13;4.74 7921.03;4.8 7918.63;5.231 7921.15;5.435 7918.99;5.556 7920.79;5.806 7923.25;6 7914.46;6.175 7920.56;6.5 7917.7;7.111 7920.15;7.856 7923.36;8 7920.84;8.515 7916.18;8.591 7921.81;8.667 7917.81;12 7909.77;12.531 7914.31;13.567 7920.53;14.712 7917.96;17.719 7920.19;19.822 7919.85;20.392 7917.64;21.93 7916.15;22.064 7913.26;23.473 7910.28;24 7913.97;26 7914.06];     % translates an amplitude (degrees) to a resonant frequency (Hz)
amplitudeLUT = zeros(0,2);     % translates a nominal amplitude (degrees) to an output amplitude (degrees)

%% dabs.generic.GalvoPureAnalog (X Galvo P2)
AOControl = '/vDAQ0/AO6';     % control terminal  e.g. '/vDAQ0/AO0'
AOOffset = '';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI6';     % feedback terminal e.g. '/vDAQ0/AI0'

angularRange = 55;     % total angular range in optical degrees (e.g. for a galvo with -20..+20 optical degrees, enter 40)
voltsPerOpticalDegrees = -0.204;     % volts per optical degrees for the control signal
voltsOffset = 0;     % voltage to be added to the output
parkPosition = 27.5;     % park position in optical degrees
slewRateLimit = Inf;     % Slew rate limit of the analog output in Volts per second

% Calibration settings
feedbackVoltLUT = [-2.32155 -5.61;-1.80599 -4.36333;-1.29006 -3.11667;-0.774207 -1.87;-0.258627 -0.623333;0.25766 0.623333;0.773425 1.87;1.28925 3.11667;1.80432 4.36333;2.32106 5.61];     % [Nx2] lut translating feedback volts into position volts
offsetVoltScaling = 1;     % scalar factor for offset volts

%% dabs.generic.GalvoPureAnalog (Y Galvo P2)
AOControl = '/vDAQ0/AO7';     % control terminal  e.g. '/vDAQ0/AO0'
AOOffset = '';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI7';     % feedback terminal e.g. '/vDAQ0/AI0'

angularRange = 55;     % total angular range in optical degrees (e.g. for a galvo with -20..+20 optical degrees, enter 40)
voltsPerOpticalDegrees = 0.2;     % volts per optical degrees for the control signal
voltsOffset = 0.6;     % voltage to be added to the output
parkPosition = 27.5;     % park position in optical degrees
slewRateLimit = Inf;     % Slew rate limit of the analog output in Volts per second

% Calibration settings
feedbackVoltLUT = [-1.98048 -4.9;-1.48637 -3.67778;-0.992303 -2.45556;-0.49855 -1.23333;-0.00485229 -0.0111111;0.489856 1.21111;0.983447 2.43333;1.47741 3.65556;1.97138 4.87778;2.46561 6.1];     % [Nx2] lut translating feedback volts into position volts
offsetVoltScaling = 1;     % scalar factor for offset volts

%% dabs.generic.DigitalShutter (920nm P2)
DOControl = '/vDAQ0/D1.2';     % control terminal  e.g. '/vDAQ0/DIO0'
invertOutput = false;     % invert output drive signal to shutter
openTime_s = 0.5;     % settling time for shutter in seconds
shutterTarget = 'Excitation';     % one of {', 'Excitation', 'Detection'}

%% dabs.generic.BeamModulatorFastAnalog (920nm P2 Power)
AOControl = '/vDAQ0/AO8';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI8';     % feedback terminal e.g. '/vDAQ0/AI0'

outputRange_V = [0 5];     % Control output range in Volts
feedbackUsesRejectedLight = false;     % Indicates if photodiode is in rejected path of beams modulator.
calibrationOpenShutters = {'920nm P2'};     % List of shutters to open during the calibration. (e.g. {'Shutter1' 'Shutter2'}

powerFractionLimit = 1;     % Maximum allowed power fraction (between 0 and 1)

% Calibration data
powerFraction2ModulationVoltLut = [0 0.10101;6.95674e-06 0.151515;1.68536e-05 0.20202;9.69553e-05 0.252525;0.00034722 0.30303;0.00084018 0.353535;0.0016445 0.40404;0.00279365 0.454545;0.00431725 0.505051;0.00624771 0.555556;0.0085346 0.606061;0.0112492 0.656566;0.0143453 0.707071;0.0178832 0.757576;0.0218286 0.808081;0.0262274 0.858586;0.0310447 0.909091;0.036374 0.959596;0.0420926 1.0101;0.0483326 1.06061;0.0549887 1.11111;0.0621711 1.16162;0.0698518 1.21212;0.0779678 1.26263;0.0864676 1.31313;0.095331 1.36364;0.10469 1.41414;0.114611 1.46465;0.125048 1.51515;0.135972 1.56566;0.147227 1.61616;0.158793 1.66667;0.171045 1.71717;0.183697 1.76768;0.196652 1.81818;0.21005 1.86869;0.223704 1.91919;0.238167 1.9697;0.252443 2.0202;0.267241 2.07071;0.282422 2.12121;0.298001 2.17172;0.314231 2.22222;0.330289 2.27273;0.346548 2.32323;0.363315 2.37374;0.380243 2.42424;0.397682 2.47475;0.415411 2.52525;0.433006 2.57576;0.451202 2.62626;0.469062 2.67677;0.487132 2.72727;0.505262 2.77778;0.523761 2.82828;0.542482 2.87879;0.560691 2.92929;0.578591 2.9798;0.596422 3.0303;0.614097 3.08081;0.631352 3.13131;0.648532 3.18182;0.665469 3.23232;0.682113 3.28283;0.699284 3.33333;0.71548 3.38384;0.731212 3.43434;0.74659 3.48485;0.761807 3.53535;0.776313 3.58586;0.790527 3.63636;0.804794 3.68687;0.819401 3.73737;0.833531 3.78788;0.847223 3.83838;0.860233 3.88889;0.872822 3.93939;0.883923 3.9899;0.894453 4.0404;0.904121 4.09091;0.913887 4.14141;0.923458 4.19192;0.932441 4.24242;0.939855 4.29293;0.947976 4.34343;0.955185 4.39394;0.96276 4.44444;0.969611 4.49495;0.976104 4.54545;0.981033 4.59596;0.984836 4.64646;0.988081 4.69697;0.990704 4.74747;0.993699 4.79798;0.995714 4.84848;0.997422 4.89899;0.998735 4.94949;1 5];
powerFraction2PowerWattLut = [0 0;1 0.235];
powerFraction2FeedbackVoltLut = [0 0.0192981;1 1.99158];
feedbackOffset_V = 0;

% Calibration settings
calibrationNumPoints = 100;     % number of equidistant points to measure within the analog output range
calibrationAverageSamples = 5;     % per analog output voltage, average N analog input samples. This helps to reduce noise
calibrationNumRepeats = 5;     % number of times to repeat the calibration routine. the end result is the average of all calibration runs
calibrationSettlingTime_s = 0.001;     % pause between measurement points. this allows the beam modulation to settle
calibrationFlybackTime_s = 0.2;     % pause between calibration runs

% Advanced Settings. Note: these settings are unused for vDAQ based systems
modifiedLineClockIn = '';     % Terminal to which external beam trigger is connected. Leave empty for automatic routing via PXI/RTSI bus
frameClockIn = '';     % Terminal to which external frame clock is connected. Leave empty for automatic routing via PXI/RTSI bus
referenceClockIn = '';     % Terminal to which external reference clock is connected. Leave empty for automatic routing via PXI/RTSI bus
referenceClockRate = 1e+07;     % if referenceClockIn is used, referenceClockRate defines the rate of the reference clock in Hz. Default: 10e6Hz

%% dabs.generic.DigitalShutter (1050nm P2)
DOControl = '/vDAQ0/D1.4';     % control terminal  e.g. '/vDAQ0/DIO0'
invertOutput = false;     % invert output drive signal to shutter
openTime_s = 0.5;     % settling time for shutter in seconds
shutterTarget = 'Excitation';     % one of {', 'Excitation', 'Detection'}

%% dabs.generic.BeamModulatorFastAnalog (1050nm P2 Power)
AOControl = '/vDAQ0/AO10';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI8';     % feedback terminal e.g. '/vDAQ0/AI0'

outputRange_V = [0 5];     % Control output range in Volts
feedbackUsesRejectedLight = false;     % Indicates if photodiode is in rejected path of beams modulator.
calibrationOpenShutters = {'1050nm P2'};     % List of shutters to open during the calibration. (e.g. {'Shutter1' 'Shutter2'}

powerFractionLimit = 1;     % Maximum allowed power fraction (between 0 and 1)

% Calibration data
powerFraction2ModulationVoltLut = [0 0.10101;3.26672e-05 0.151515;7.85049e-05 0.20202;0.000271201 0.252525;0.000724386 0.30303;0.00150698 0.353535;0.00267989 0.40404;0.00422728 0.454545;0.00624161 0.505051;0.00864499 0.555556;0.0114722 0.606061;0.0147725 0.656566;0.0184926 0.707071;0.0226587 0.757576;0.0272825 0.808081;0.0323363 0.858586;0.0378327 0.909091;0.0437658 0.959596;0.0501453 1.0101;0.0570258 1.06061;0.0642903 1.11111;0.0721877 1.16162;0.080381 1.21212;0.0891244 1.26263;0.0983522 1.31313;0.10801 1.36364;0.117998 1.41414;0.128572 1.46465;0.139503 1.51515;0.150723 1.56566;0.162295 1.61616;0.174596 1.66667;0.187263 1.71717;0.200426 1.76768;0.213691 1.81818;0.227591 1.86869;0.241803 1.91919;0.256381 1.9697;0.271308 2.0202;0.286449 2.07071;0.30179 2.12121;0.317688 2.17172;0.33374 2.22222;0.350106 2.27273;0.366301 2.32323;0.382673 2.37374;0.399186 2.42424;0.415963 2.47475;0.432726 2.52525;0.449707 2.57576;0.466387 2.62626;0.482847 2.67677;0.499373 2.72727;0.51572 2.77778;0.5318 2.82828;0.548327 2.87879;0.564402 2.92929;0.580956 2.9798;0.59706 3.0303;0.613502 3.08081;0.629243 3.13131;0.645397 3.18182;0.660361 3.23232;0.675554 3.28283;0.690266 3.33333;0.704899 3.38384;0.719254 3.43434;0.73326 3.48485;0.746458 3.53535;0.759531 3.58586;0.772341 3.63636;0.784668 3.68687;0.797041 3.73737;0.808382 3.78788;0.820482 3.83838;0.831645 3.88889;0.842297 3.93939;0.853564 3.9899;0.863597 4.0404;0.87299 4.09091;0.882397 4.14141;0.89203 4.19192;0.901774 4.24242;0.909833 4.29293;0.918765 4.34343;0.927092 4.39394;0.936285 4.44444;0.943075 4.49495;0.949721 4.54545;0.956293 4.59596;0.962371 4.64646;0.968027 4.69697;0.972823 4.74747;0.979513 4.79798;0.984988 4.84848;0.990781 4.89899;0.995119 4.94949;1 5];
powerFraction2PowerWattLut = [0 0;1 0.178];
powerFraction2FeedbackVoltLut = [0 0.0307467;1 1.86216];
feedbackOffset_V = 0;

% Calibration settings
calibrationNumPoints = 100;     % number of equidistant points to measure within the analog output range
calibrationAverageSamples = 5;     % per analog output voltage, average N analog input samples. This helps to reduce noise
calibrationNumRepeats = 5;     % number of times to repeat the calibration routine. the end result is the average of all calibration runs
calibrationSettlingTime_s = 0.001;     % pause between measurement points. this allows the beam modulation to settle
calibrationFlybackTime_s = 0.2;     % pause between calibration runs

% Advanced Settings. Note: these settings are unused for vDAQ based systems
modifiedLineClockIn = '';     % Terminal to which external beam trigger is connected. Leave empty for automatic routing via PXI/RTSI bus
frameClockIn = '';     % Terminal to which external frame clock is connected. Leave empty for automatic routing via PXI/RTSI bus
referenceClockIn = '';     % Terminal to which external reference clock is connected. Leave empty for automatic routing via PXI/RTSI bus
referenceClockRate = 1e+07;     % if referenceClockIn is used, referenceClockRate defines the rate of the reference clock in Hz. Default: 10e6Hz

%% scanimage.components.scan2d.RggScan (RGG_P2)

acquisitionDeviceId = 'vDAQ0';     % RDI Device ID
acquisitionEngineIdx = 2;

resonantScanner = 'X Reso P2';     % Name of the resonant scanner
xGalvo = 'X Galvo P2';     % Name of the x galvo scanner
yGalvo = 'Y Galvo P2';     % Name of the y galvo scanner
beams = {'920nm P2 Power' '1050nm P2 Power'};     % beam device names
fastZs = {};     % fastZ device names
shutters = {'920nm P2' '1050nm P2'};     % shutter device names

channelsInvert = [true true];     % Logical: Specifies if the input signal is inverted (i.e., more negative for increased light signal)
keepResonantScannerOn = false;     % Always keep resonant scanner on to avoid drift and settling time issues
desiredSampleRateCtl = 200000;     % Sampling rate to use for galvo, beams, and fastZ control

externalSampleClock = true;     % Logical: use external sample clock connected to the CLK IN terminal of the FlexRIO digitizer module
externalSampleClockRate = 7.89e+07;     % [Hz]: nominal frequency of the external sample clock connected to the CLK IN terminal (e.g. 80e6); actual rate is measured on FPGA
externalSampleClockMultiplier = 32;     % Multiplier to apply to external sample clock
useCustomFilterClock = false;
customFilterClockPeriod = 32;
sampleClockPhase = [];     % Phase delay to apply to sample clock

extendedRggFov = 1;     % If true and x galvo is present, addressable FOV is combination of resonant FOV and x galvo FOV.

% Advanced/Optional
PeriodClockDebounceTime = 1e-07;     % [s] time the period clock has to be stable before a change is registered
TriggerDebounceTime = 5e-07;     % [s] time acquisition, stop and next trigger to be stable before a change is registered
reverseLineRead = 1;     % flips the image in the resonant scan axis
defaultFlybackTimePerFrame = 0.001;     % [s] default time to allow galvos to fly back after one frame is complete. overridden by cfg file
defaultFlytoTimePerScanfield = 0.001;     % [s] time to allow galvos to fly from one scanfield to the next. overridden by cfg file

% Aux Trigger Recording, Photon Counting, and I2C are mutually exclusive

% Aux Trigger Recording
auxTriggersTimeDebounce = 1e-07;     % [s] time after an edge where subsequent edges are ignored
auxTriggerLinesInvert = [false false false false];     % [logical] 1x4 vector specifying polarity of aux trigger inputs
auxTrigger1In = '';     % Digital input lines for aux trigger 1
auxTrigger2In = '';     % Digital input lines for aux trigger 2
auxTrigger3In = '';     % Digital input lines for aux trigger 3
auxTrigger4In = '';     % Digital input lines for aux trigger 4

% Signal Conditioning
disableMaskDivide = [false false false];     % disable averaging of samples into pixels; instead accumulate samples
photonDiscriminatorThresholds = [500 1000];
photonDiscriminatorModes = {'threshold crossing' 'threshold crossing'};
photonDiscriminatorDifferentiateWidths = [4 4];
enableFpgaPixelCorrection = 0;     % Enable constant coefficient pixel multiplication on the vDAQ FPGA
fpgaPixelCorrectionMultiplier = 'MAXBIN';     % Numeric or string, e.g. 500, 'MAXBIN', 'MINBIN', 'MEANBIN'
enableHostPixelCorrection = false;     % Enable constant coefficient pixel multiplication on the host PC
hostPixelCorrectionMultiplier = 500;     % Numeric or string, e.g. 500, 'MAXBIN', 'MINBIN', 'MEANBIN'

% I2C
i2cEnable = false;
i2cSdaPort = '';
i2cSclPort = '';
i2cAddress = 0;     % [byte] I2C address of the FPGA
i2cDebounce = 1e-07;     % [s] time the I2C signal has to be stable high before a change is registered
i2cStoreAsChar = false;     % if false, the I2C packet bytes are stored as a uint8 array. if true, the I2C packet bytes are stored as a string. Note: a Null byte in the packet terminates the string
i2cSendAck = true;     % When enabled FPGA confirms each packet with an ACK bit by actively pulling down the SDA line

% Laser Trigger
LaserTriggerPort = '/vDAQ0/CLK_IN';     % Digital input where laser trigger is connected.
LRRLaserSyncPort = '';     % Digital input where laser trigger is daisy chained (when plugged into CLK_IN) when using lower repetition rate lasers.

% Trigger Outputs
frameClockOut = '/vDAQ0/D3.3';     % Output line for the frame clock
lineClockOut = '/vDAQ0/D3.5';     % Output line for the line clock
beamModifiedLineClockOut = '';     % Output line for beam clock
volumeTriggerOut = '';     % Output line for the volume clock

% Calibration data
scannerToRefTransform = [1 0 0;0 1 0;0 0 1];
LaserTriggerDebounceTicks = 1;
virtualChannelsSource = {'AI0' 'AI1' 'AI1'};
virtualChannelsMode = {'analog' 'analog' 'photon counting'};
virtualChannelsThreshold = [false false false];
virtualChannelsBinarize = [false false false];
virtualChannelsEdgeDetect = [false false false];
virtualChannelsLaserGate = [false false false];
virtualChannelsDisableDivide = [false false false];
virtualChannelsThresholdValue = [100 100 100];
virtualChannelsLaserFilterWindow = {[0 1] [21 24] [0 1]};


%% dabs.generic.recolorGuis (Recolor P2)
scanners = {'RGG_P2'};     % Order of imaging scanners associated with the order of colors. Ordinarily, this should not be modified by the user.
colors = {[1 0 1]};     % Cell array or RGB vectors. Cell ordering corresponds to scanner order above.

%% dabs.resources.daqs.vDAQ_Config (Passive vDAQ Initialization)
vdaqNumber = 0;     % ID number of vDAQ board this configuration should apply to
serialNumber = 'DE3DC902';     % Serial number of vDAQ board
bitfileName = 'vDAQR1_SI.dbs';     % Custom bitfile to load
passiveMode = 1;     % Prevent re-initializing of vDAQ if there is already a loaded design

