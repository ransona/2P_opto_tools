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

pairStimActiveOutputChannel = 0;
pairBeamActiveOutputChannel = 0;

%% dabs.generic.ResonantScannerAnalog (X Reso P1)
AOZoom = '/Dev1/AO0';     % zoom control terminal  e.g. '/vDAQ0/AO0'
DOEnable = '/vDAQ0/D0.1';     % digital enable terminal e.g. '/vDAQ0/D0.1'
DISync = '/vDAQ0/D0.0';     % digital sync terminal e.g. '/vDAQ0/D0.0'

nominalFrequency = 7910;     % nominal resonant frequency in Hz
angularRange = 24;     % total angular range in optical degrees (e.g. for a resonant scanner with -13..+13 optical degrees, enter 26)
voltsPerOpticalDegrees = 0.1923;     % volts per optical degrees for the control signal
settleTime = 0.5;     % settle time in seconds to allow the resonant scanner to turn on

% Calibration Settings
amplitudeToLinePhaseMap = [4 -2.53485e-06;6 -2.62357e-06;12 -2.69962e-06;17.615 -2.69962e-06;18.767 -2.76299e-06;19.449 -2.76299e-06;24 -2.76299e-06];     % translates an amplitude (degrees) to a line phase (seconds)
amplitudeToFrequencyMap = [4 7926.61;4.228 7922.59;4.8 7926.55;4.996 7926.24;5.2 7927.36;5.523 7925.22;6 7921.03;6.405 7924.5;8.327 7926.26;8.711 7927.27;12 7915.08;12.769 7923.96;14.348 7926.26;16.552 7916.65;17.202 7917.39;17.295 7918.32;17.615 7921.82;18.767 7916.94;19.141 7917.25;19.449 7919.14;19.537 7918.85;19.833 7917.37;21.163 7920.57;21.636 7918.5;21.907 7923.76;23.059 7923.15;23.444 7924.66;24 7918.82];     % translates an amplitude (degrees) to a resonant frequency (Hz)
amplitudeLUT = zeros(0,2);     % translates a nominal amplitude (degrees) to an output amplitude (degrees)

%% dabs.generic.GalvoPureAnalog (X Galvo P1)
AOControl = '/vDAQ0/AO0';     % control terminal  e.g. '/vDAQ0/AO0'
AOOffset = '';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI0';     % feedback terminal e.g. '/vDAQ0/AI0'

angularRange = 50;     % total angular range in optical degrees (e.g. for a galvo with -20..+20 optical degrees, enter 40)
voltsPerOpticalDegrees = 0.204;     % volts per optical degrees for the control signal
voltsOffset = 1.6;     % voltage to be added to the output
parkPosition = 25;     % park position in optical degrees
slewRateLimit = Inf;     % Slew rate limit of the analog output in Volts per second

% Calibration settings
feedbackVoltLUT = [-2.32922 -5.7;-1.86537 -4.56667;-1.4028 -3.43333;-0.939255 -2.3;-0.477017 -1.16667;-0.0138062 -0.0333333;0.449576 1.1;0.91265 2.23333;1.37611 3.36667;1.83982 4.5];     % [Nx2] lut translating feedback volts into position volts
offsetVoltScaling = 1;     % scalar factor for offset volts

%% dabs.generic.GalvoPureAnalog (Y Galvo P1)
AOControl = '/vDAQ0/AO1';     % control terminal  e.g. '/vDAQ0/AO0'
AOOffset = '';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI1';     % feedback terminal e.g. '/vDAQ0/AI0'

angularRange = 55;     % total angular range in optical degrees (e.g. for a galvo with -20..+20 optical degrees, enter 40)
voltsPerOpticalDegrees = 0.2;     % volts per optical degrees for the control signal
voltsOffset = 0.4;     % voltage to be added to the output
parkPosition = 27.5;     % park position in optical degrees
slewRateLimit = Inf;     % Slew rate limit of the analog output in Volts per second

% Calibration settings
feedbackVoltLUT = [-2.01853 -4.8;-1.50325 -3.57778;-0.990549 -2.35556;-0.47645 -1.13333;0.0382416 0.0888889;0.553125 1.31111;1.06682 2.53333;1.58073 3.75556;2.09469 4.97778;2.60873 6.2];     % [Nx2] lut translating feedback volts into position volts
offsetVoltScaling = 1;     % scalar factor for offset volts

%% dabs.generic.FastZPureAnalog (ETL P1)
AOControl = '/vDAQ0/AO4';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '';     % feedback terminal e.g. '/vDAQ0/AI0'
FrameClockIn = '';     % frame clock input terminal e.g. '/Dev1/PFI0'

parkPositionUm = 0;     % park position in micron
travelRangeUm = [-350 350];     % travel range in micron

voltsPerUm = -0.006;     % volts per micron
voltsOffset = 6.15;     % volts that sets actuator to zero position

% Calibration Data
positionLUT = zeros(0,2);     % Position LUT
feedbackVoltLUT = zeros(0,2);     % [Nx2] lut translating feedback volts into position volts

%% dabs.generic.DigitalShutter (920nm P1)
DOControl = '/vDAQ0/D0.2';     % control terminal  e.g. '/vDAQ0/DIO0'
invertOutput = false;     % invert output drive signal to shutter
openTime_s = 0.5;     % settling time for shutter in seconds
shutterTarget = 'Excitation';     % one of {', 'Excitation', 'Detection'}

%% dabs.generic.BeamModulatorFastAnalog (920nm P1 Power)
AOControl = '/vDAQ0/AO2';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI2';     % feedback terminal e.g. '/vDAQ0/AI0'

outputRange_V = [0 5];     % Control output range in Volts
feedbackUsesRejectedLight = false;     % Indicates if photodiode is in rejected path of beams modulator.
calibrationOpenShutters = {'920nm P1'};     % List of shutters to open during the calibration. (e.g. {'Shutter1' 'Shutter2'}

powerFractionLimit = 1;     % Maximum allowed power fraction (between 0 and 1)

% Calibration data
powerFraction2ModulationVoltLut = [0 0.252525;0.000541806 0.30303;0.00190751 0.353535;0.00333682 0.40404;0.00516537 0.454545;0.00743246 0.505051;0.0100193 0.555556;0.0134368 0.606061;0.0162597 0.656566;0.0210668 0.707071;0.0251929 0.757576;0.0309474 0.808081;0.0361016 0.858586;0.0427536 0.909091;0.0489551 0.959596;0.0565911 1.0101;0.0641152 1.06061;0.0726683 1.11111;0.0812269 1.16162;0.0911186 1.21212;0.1021 1.26263;0.11358 1.31313;0.124972 1.36364;0.137141 1.41414;0.150377 1.46465;0.163051 1.51515;0.177176 1.56566;0.190292 1.61616;0.205357 1.66667;0.219387 1.71717;0.235185 1.76768;0.250437 1.81818;0.267416 1.86869;0.283085 1.91919;0.300141 1.9697;0.317239 2.0202;0.335689 2.07071;0.353764 2.12121;0.373059 2.17172;0.391092 2.22222;0.410556 2.27273;0.428769 2.32323;0.447813 2.37374;0.466492 2.42424;0.485466 2.47475;0.504678 2.52525;0.52424 2.57576;0.543724 2.62626;0.563233 2.67677;0.582763 2.72727;0.601921 2.77778;0.621382 2.82828;0.639607 2.87879;0.658174 2.92929;0.675529 2.9798;0.693468 3.0303;0.71084 3.08081;0.72775 3.13131;0.744301 3.18182;0.760619 3.23232;0.776611 3.28283;0.793063 3.33333;0.808388 3.38384;0.823558 3.43434;0.837602 3.48485;0.852179 3.53535;0.865682 3.58586;0.878484 3.63636;0.890768 3.68687;0.902468 3.73737;0.913371 3.78788;0.923794 3.83838;0.934112 3.88889;0.942952 3.93939;0.952177 3.9899;0.958882 4.0404;0.966597 4.09091;0.972367 4.14141;0.978728 4.19192;0.98325 4.24242;0.988003 4.29293;0.991633 4.34343;0.99505 4.39394;0.996993 4.44444;0.998987 4.49495;0.999528 4.54545;1 4.59596];
powerFraction2PowerWattLut = [0 0;1 0.245];
powerFraction2FeedbackVoltLut = [0 0.201043;1 9.19846];
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

%% dabs.generic.DigitalShutter (1050nm P1)
DOControl = '/vDAQ0/D0.3';     % control terminal  e.g. '/vDAQ0/DIO0'
invertOutput = false;     % invert output drive signal to shutter
openTime_s = 0.5;     % settling time for shutter in seconds
shutterTarget = 'Excitation';     % one of {', 'Excitation', 'Detection'}

%% dabs.generic.BeamModulatorFastAnalog (1050nm P1 Power)
AOControl = '/vDAQ0/AO3';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI2';     % feedback terminal e.g. '/vDAQ0/AI0'

outputRange_V = [0 5];     % Control output range in Volts
feedbackUsesRejectedLight = false;     % Indicates if photodiode is in rejected path of beams modulator.
calibrationOpenShutters = {'1050nm P1'};     % List of shutters to open during the calibration. (e.g. {'Shutter1' 'Shutter2'}

powerFractionLimit = 1;     % Maximum allowed power fraction (between 0 and 1)

% Calibration data
powerFraction2ModulationVoltLut = [0 0.454545;0.0033255 0.505051;0.00570643 0.555556;0.00957265 0.606061;0.013705 0.656566;0.0163106 0.707071;0.0207937 0.757576;0.0263175 0.808081;0.0305944 0.858586;0.0389062 0.909091;0.0439922 0.959596;0.0524419 1.0101;0.0592957 1.06061;0.0657901 1.11111;0.0765712 1.16162;0.0824039 1.21212;0.0937206 1.26263;0.104186 1.31313;0.113315 1.36364;0.127591 1.41414;0.135614 1.46465;0.150991 1.51515;0.162885 1.56566;0.175634 1.61616;0.190362 1.66667;0.201219 1.71717;0.217543 1.76768;0.229975 1.81818;0.24728 1.86869;0.262753 1.91919;0.279585 1.9697;0.295171 2.0202;0.311488 2.07071;0.328326 2.12121;0.344146 2.17172;0.363248 2.22222;0.379526 2.27273;0.39839 2.32323;0.414437 2.37374;0.432275 2.42424;0.451718 2.47475;0.466882 2.52525;0.486427 2.57576;0.504731 2.62626;0.519446 2.67677;0.541723 2.72727;0.558015 2.77778;0.574784 2.82828;0.593514 2.87879;0.608514 2.92929;0.628294 2.9798;0.643822 3.0303;0.661693 3.08081;0.679052 3.13131;0.693271 3.18182;0.710594 3.23232;0.726084 3.28283;0.739849 3.33333;0.75804 3.38384;0.770883 3.43434;0.786542 3.48485;0.800731 3.53535;0.813369 3.58586;0.826799 3.63636;0.840949 3.68687;0.851506 3.73737;0.864753 3.78788;0.875335 3.83838;0.884432 3.88889;0.897172 3.93939;0.905217 3.9899;0.91593 4.0404;0.92472 4.09091;0.930621 4.14141;0.939585 4.19192;0.945104 4.24242;0.951854 4.29293;0.96101 4.34343;0.962686 4.39394;0.971782 4.44444;0.975581 4.49495;0.98209 4.54545;0.985094 4.59596;0.986814 4.64646;0.992488 4.69697;0.993085 4.74747;0.995459 4.79798;0.99706 4.84848;1 4.89899];
powerFraction2PowerWattLut = [0 0;1 0.228];
powerFraction2FeedbackVoltLut = [0 0.0322206;1 1.59271];
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

%% dabs.generic.PMTAnalog (Green PMT)
AOGain = '/Dev2/AO0';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/AO0)
AOOutputRange = [0 0.9];     % <required if AOGain is defined> array of 1x2 numeric array specifying the minimum and maximum analog output voltage on the DAQ board that controls the PMT gain.
SupplyVoltageRange = [0 0.9];     % <required if AOGain is defined> array of 1x2 specifying the minimum and maximum for the PMT power supply in Volts.

DOPower = '';     % <optional> resource name of the digital output channel that switches the PMT on/off (e.g. /vDAQ0/D0.0)
DITripDetect = '';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/D0.1)
DOTripReset = '';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/D0.2)

% Calibration settings
wavelength_nm = 510;     % wavelength in nanometer
autoOn = false;     % powers the PMT automatically on for the duration of a scan
gain_V = 0.6;     % PMT power supply voltage

%% dabs.generic.PMTAnalog (Red PMT)
AOGain = '/Dev2/AO1';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/AO0)
AOOutputRange = [0 0.9];     % <required if AOGain is defined> array of 1x2 numeric array specifying the minimum and maximum analog output voltage on the DAQ board that controls the PMT gain.
SupplyVoltageRange = [0 0.9];     % <required if AOGain is defined> array of 1x2 specifying the minimum and maximum for the PMT power supply in Volts.

DOPower = '';     % <optional> resource name of the digital output channel that switches the PMT on/off (e.g. /vDAQ0/D0.0)
DITripDetect = '';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/D0.1)
DOTripReset = '';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/D0.2)

% Calibration settings
wavelength_nm = 630;     % wavelength in nanometer
autoOn = false;     % powers the PMT automatically on for the duration of a scan
gain_V = 0.6;     % PMT power supply voltage

%% scanimage.components.scan2d.RggScan (RGG)

acquisitionDeviceId = 'vDAQ0';     % RDI Device ID
acquisitionEngineIdx = 1;

resonantScanner = 'X Reso P1';     % Name of the resonant scanner
xGalvo = 'X Galvo P1';     % Name of the x galvo scanner
yGalvo = 'Y Galvo P1';     % Name of the y galvo scanner
beams = {'920nm P1 Power' '1050nm P1 Power'};     % beam device names
fastZs = {'ETL P1'};     % fastZ device names
shutters = {'920nm P1' '1050nm P1'};     % shutter device names

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
reverseLineRead = 0;     % flips the image in the resonant scan axis
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
photonDiscriminatorThresholds = [500 500];
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
frameClockOut = '/vDAQ0/D3.2';     % Output line for the frame clock
lineClockOut = '/vDAQ0/D3.4';     % Output line for the line clock
beamModifiedLineClockOut = '';     % Output line for beam clock
volumeTriggerOut = '';     % Output line for the volume clock

% Calibration data
scannerToRefTransform = [1 0 0;0 1 0;0 0 1];
LaserTriggerDebounceTicks = 1;
virtualChannelsSource = {'AI0' 'AI1' 'AI0'};
virtualChannelsMode = {'analog' 'analog' 'analog'};
virtualChannelsThreshold = [false false false];
virtualChannelsBinarize = [false false false];
virtualChannelsEdgeDetect = [false false false];
virtualChannelsLaserGate = [false false false];
virtualChannelsDisableDivide = [false false false];
virtualChannelsThresholdValue = [100 100 100];
virtualChannelsLaserFilterWindow = {[0 1] [0 1] [0 1]};

%% dabs.zaber.ZaberMultiDevice (Zaber Motor)
comPort = 'COM3';     % Serial port the stage is connected to (e.g. 'COM3')
baudRate = 115200;     % Baudrate for serial communication
communicationProtocol = 'ASCII';     % Communication protocol ('ASCII' or 'Binary')
deviceLibraryPath = '';     % Path to '.sqlite' device library. Only required for offline use.
homingTimeout_s = 20;     % Timeout for homing move in seconds

%% dabs.generic.recolorGuis (Recolor P1)
scanners = {'RGG'};     % Order of imaging scanners associated with the order of colors. Ordinarily, this should not be modified by the user.
colors = {[0.466667 0.67451 0.188235]};     % Cell array or RGB vectors. Cell ordering corresponds to scanner order above.

