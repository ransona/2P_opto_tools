% Most Software Machine Data File

%% scanimage.SI (ScanImage)

% Global microscope properties
objectiveResolution = 76.8833;     % Resolution of the objective in microns/degree of scan angle

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
photostimScannerName = 'RGG_P2';     % Name of scanner (from first MDF section) to use for photostimulation. Must be a linear scanner
stimTriggerTerm = '';     % Specifies the channel that should be used to trigger a stimulation. This a triggering port name such as D2.1 for vDAQ or PFI1 for the auxiliary IO board of an NI LinScan system.

% Monitoring DAQ AI channels
BeamAiId = '';     % AI channel to be used for monitoring the Pockels cell output

loggingStartTrigger = '';     % PFI line to which start trigger for logging is wired to photostim board. Leave empty for automatic routing via PXI bus

stimActiveOutputChannel = '/vDAQ0/D0.5';     % Digital terminal on stim board to output stim active signal. (e.g. on vDAQ: 'D2.6' on NI-DAQ hardware: '/port0/line0'
beamActiveOutputChannel = '';     % Digital terminal on stim board to output beam active signal. (e.g. on vDAQ: 'D2.7' on NI-DAQ hardware: '/port0/line1'
slmTriggerOutputChannel = '';     % Digital terminal on stim board to trigger SLM frame flip. (e.g. on vDAQ: 'D2.5' on NI-DAQ hardware: '/port0/line2'

pairStimActiveOutputChannel = 1;
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
amplitudeToLinePhaseMap = [1.19 -1.8048e-06;1.316 -1.8816e-06;1.786 -2.112e-06;1.846 -2.15463e-06;1.923 -2.2144e-06;2.349 -2.69962e-06;2.4 -2.28137e-06;2.5 -2.3168e-06;3 -2.38276e-06;3.429 -2.48416e-06;4 -2.53485e-06;4.167 -2.5344e-06;4.8 -2.5344e-06;5 -2.5728e-06;6 -2.5602e-06;8 -2.6109e-06;8.333 -2.6496e-06;12 -2.72497e-06;24 -2.73764e-06;25 -2.8032e-06];     % translates an amplitude (degrees) to a line phase (seconds)
amplitudeToFrequencyMap = [1.333 7923.69;1.714 7924.26;1.846 7921.01;2 7924.66;2.083 7924.12;2.349 7927.37;2.4 7918.36;2.5 7921.72;3 7922.43;3.25 7923.28;3.429 7920.22;3.523 7927.27;3.789 7923.12;4 7924.47;4.8 7924.46;5.2 7922.66;6 7919.36;6.909 7920.34;6.918 7926.27;8 7921.19;8.968 7926.61;10.909 7922.74;12 7919.21;12.122 7916.85;14.092 7926.01;16 7925.66;16.526 7915.86;17.377 7923.95;18.147 7915.07;19.2 7925.12;19.36 7919.19;20 7915.07;21.202 7917.44;21.774 7918.6;22.035 7911.17;22.715 7920.29;23.473 7916.01;23.572 7918.72;23.638 7919.4;23.679 7915.3;23.956 7911.38;24 7923.01;25 7920.74;25.999 7920.34;26 7919.65;28 7916.27];     % translates an amplitude (degrees) to a resonant frequency (Hz)
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
feedbackVoltLUT = [-1.43067 -3.5;-0.967981 -2.36667;-0.503864 -1.23333;-0.0419037 -0.1;0.421701 1.03333;0.885596 2.16667;1.34928 3.3;1.81216 4.43333;2.27537 5.56667;2.73817 6.7];     % [Nx2] lut translating feedback volts into position volts
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
feedbackVoltLUT = [-2.14462 -5.1;-1.62962 -3.87778;-1.11559 -2.65556;-0.601846 -1.43333;-0.0877045 -0.211111;0.426965 1.01111;0.939594 2.23333;1.45497 3.45556;1.96803 4.67778;2.48169 5.9];     % [Nx2] lut translating feedback volts into position volts
offsetVoltScaling = 1;     % scalar factor for offset volts

minimizeOnStart = false;

%% dabs.generic.ResonantScannerAnalog (X Reso P2)
AOZoom = '/Dev1/AO1';     % zoom control terminal  e.g. '/vDAQ0/AO0'
DOEnable = '/vDAQ0/D1.1';     % digital enable terminal e.g. '/vDAQ0/D0.1'
DISync = '/vDAQ0/D1.0';     % digital sync terminal e.g. '/vDAQ0/D0.0'

nominalFrequency = 7910;     % nominal resonant frequency in Hz
angularRange = 24;     % total angular range in optical degrees (e.g. for a resonant scanner with -13..+13 optical degrees, enter 26)
voltsPerOpticalDegrees = 0.1923;     % volts per optical degrees for the control signal
settleTime = 0.5;     % settle time in seconds to allow the resonant scanner to turn on

% Calibration Settings
amplitudeToLinePhaseMap = [1.333 -5.06971e-08;2 -2.40811e-07;2.6 -2.816e-07;2.667 -3.16857e-07;3 -4.352e-07;3.429 -3.584e-07;4 -4.81622e-07;4.798 3.92902e-07;4.8 -2.432e-07;6 -4.608e-07;8 -5.504e-07;8.667 -5.504e-07;11.994 -3.92902e-07;12 -6.21039e-07;14.712 -5.888e-07;24 -6.21039e-07;26 -6.144e-07];     % translates an amplitude (degrees) to a line phase (seconds)
amplitudeToFrequencyMap = [1.333 7920.57;2 7917.02;2.6 7918.94;2.615 7922.26;2.667 7916.58;3 7919.64;3.429 7919.94;3.433 7921.74;4 7918.97;4.74 7921.03;4.8 7920.48;5.231 7921.15;5.435 7918.99;5.556 7920.79;6 7914.46;6.175 7920.56;6.5 7917.7;7.111 7920.15;8 7921.21;8.667 7917.81;12 7917.86;13.567 7920.53;14.712 7917.96;20 7916.12;20.392 7917.64;23.473 7910.28;23.875 7911.57;23.926 7915.06;24 7913.97;26 7914.06];     % translates an amplitude (degrees) to a resonant frequency (Hz)
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
feedbackVoltLUT = [-2.32177 -5.61;-1.80584 -4.36333;-1.28973 -3.11667;-0.773987 -1.87;-0.258463 -0.623333;0.257635 0.623333;0.773303 1.87;1.28892 3.11667;1.80483 4.36333;2.32068 5.61];     % [Nx2] lut translating feedback volts into position volts
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
feedbackVoltLUT = [-1.98051 -4.9;-1.48634 -3.67778;-0.99259 -2.45556;-0.498685 -1.23333;-0.00465088 -0.0111111;0.489777 1.21111;0.983704 2.43333;1.47751 3.65556;1.97169 4.87778;2.46577 6.1];     % [Nx2] lut translating feedback volts into position volts
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

minimizeOnStart = true;

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
powerFraction2PowerWattLut = [0 0;1 0.269];
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

outputRange_V = [5 0];     % Control output range in Volts
feedbackUsesRejectedLight = false;     % Indicates if photodiode is in rejected path of beams modulator.
calibrationOpenShutters = {'1050nm P1'};     % List of shutters to open during the calibration. (e.g. {'Shutter1' 'Shutter2'}

powerFractionLimit = 1;     % Maximum allowed power fraction (between 0 and 1)

% Calibration data
powerFraction2ModulationVoltLut = [0 0.454545;0.0033255 0.505051;0.00570643 0.555556;0.00957265 0.606061;0.013705 0.656566;0.0163106 0.707071;0.0207937 0.757576;0.0263175 0.808081;0.0305944 0.858586;0.0389062 0.909091;0.0439922 0.959596;0.0524419 1.0101;0.0592957 1.06061;0.0657901 1.11111;0.0765712 1.16162;0.0824039 1.21212;0.0937206 1.26263;0.104186 1.31313;0.113315 1.36364;0.127591 1.41414;0.135614 1.46465;0.150991 1.51515;0.162885 1.56566;0.175634 1.61616;0.190362 1.66667;0.201219 1.71717;0.217543 1.76768;0.229975 1.81818;0.24728 1.86869;0.262753 1.91919;0.279585 1.9697;0.295171 2.0202;0.311488 2.07071;0.328326 2.12121;0.344146 2.17172;0.363248 2.22222;0.379526 2.27273;0.39839 2.32323;0.414437 2.37374;0.432275 2.42424;0.451718 2.47475;0.466882 2.52525;0.486427 2.57576;0.504731 2.62626;0.519446 2.67677;0.541723 2.72727;0.558015 2.77778;0.574784 2.82828;0.593514 2.87879;0.608514 2.92929;0.628294 2.9798;0.643822 3.0303;0.661693 3.08081;0.679052 3.13131;0.693271 3.18182;0.710594 3.23232;0.726084 3.28283;0.739849 3.33333;0.75804 3.38384;0.770883 3.43434;0.786542 3.48485;0.800731 3.53535;0.813369 3.58586;0.826799 3.63636;0.840949 3.68687;0.851506 3.73737;0.864753 3.78788;0.875335 3.83838;0.884432 3.88889;0.897172 3.93939;0.905217 3.9899;0.91593 4.0404;0.92472 4.09091;0.930621 4.14141;0.939585 4.19192;0.945104 4.24242;0.951854 4.29293;0.96101 4.34343;0.962686 4.39394;0.971782 4.44444;0.975581 4.49495;0.98209 4.54545;0.985094 4.59596;0.986814 4.64646;0.992488 4.69697;0.993085 4.74747;0.995459 4.79798;0.99706 4.84848;1 4.89899];
powerFraction2PowerWattLut = [0 0;1 0.246];
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
powerFraction2PowerWattLut = [0 0;1 0.175];
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

minimizeOnStart = true;

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
powerFraction2PowerWattLut = [0 0;1 0.468];
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

minimizeOnStart = true;

%% dabs.generic.DigitalShutter (1035nm P2)
DOControl = '/vDAQ0/D1.3';     % control terminal  e.g. '/vDAQ0/DIO0'
invertOutput = false;     % invert output drive signal to shutter
openTime_s = 0.5;     % settling time for shutter in seconds
shutterTarget = 'Excitation';     % one of {', 'Excitation', 'Detection'}

%% dabs.generic.BeamModulatorFastAnalog (1035nm P2 Power)
AOControl = '/vDAQ0/AO9';     % control terminal  e.g. '/vDAQ0/AO0'
AIFeedback = '/vDAQ0/AI8';     % feedback terminal e.g. '/vDAQ0/AI0'

outputRange_V = [0 0.5];     % Control output range in Volts
feedbackUsesRejectedLight = false;     % Indicates if photodiode is in rejected path of beams modulator.
calibrationOpenShutters = {'1035nm P2'};     % List of shutters to open during the calibration. (e.g. {'Shutter1' 'Shutter2'}

powerFractionLimit = 1;     % Maximum allowed power fraction (between 0 and 1)

% Calibration data
powerFraction2ModulationVoltLut = [0 0;3.89819e-06 0.010101;7.20178e-05 0.0151515;7.29008e-05 0.020202;8.11253e-05 0.0252525;8.54664e-05 0.030303;8.91264e-05 0.0353535;9.61152e-05 0.040404;0.0001042 0.0454545;0.00010922 0.0505051;0.000120945 0.0555556;0.000133009 0.0606061;0.000144431 0.0656566;0.000163303 0.0707071;0.000182438 0.0757576;0.000212246 0.0808081;0.000251066 0.0858586;0.000301512 0.0909091;0.000374402 0.0959596;0.000463689 0.10101;0.000589955 0.106061;0.000756923 0.111111;0.000961998 0.116162;0.0012332 0.121212;0.00155874 0.126263;0.00199501 0.131313;0.00249799 0.136364;0.0031433 0.141414;0.0038884 0.146465;0.00482655 0.151515;0.00594007 0.156566;0.0071753 0.161616;0.00869976 0.166667;0.0103473 0.171717;0.0123353 0.176768;0.0144796 0.181818;0.0171073 0.186869;0.0198284 0.191919;0.023062 0.19697;0.0264447 0.20202;0.0304495 0.207071;0.0348501 0.212121;0.0393673 0.217172;0.0446627 0.222222;0.0500255 0.227273;0.0562052 0.232323;0.0624424 0.237374;0.0695269 0.242424;0.0767376 0.247475;0.0848701 0.252525;0.0934941 0.257576;0.102164 0.262626;0.111958 0.267677;0.12174 0.272727;0.132607 0.277778;0.143439 0.282828;0.15546 0.287879;0.167294 0.292929;0.180387 0.29798;0.193267 0.30303;0.207438 0.308081;0.222072 0.313131;0.236538 0.318182;0.252666 0.323232;0.26804 0.328283;0.285373 0.333333;0.301867 0.338384;0.319981 0.343434;0.337409 0.348485;0.356687 0.353535;0.375008 0.358586;0.395152 0.363636;0.41569 0.368687;0.435192 0.373737;0.45657 0.378788;0.476683 0.383838;0.498598 0.388889;0.519148 0.393939;0.541514 0.39899;0.562331 0.40404;0.584897 0.409091;0.607559 0.414141;0.629649 0.419192;0.65293 0.424242;0.674897 0.429293;0.698281 0.434343;0.720969 0.439394;0.745323 0.444444;0.770089 0.449495;0.797582 0.454545;0.822191 0.459596;0.849253 0.464646;0.877039 0.469697;0.903308 0.474747;0.927581 0.479798;0.948683 0.484848;0.97025 0.489899;0.987495 0.494949;1 0.5];
powerFraction2PowerWattLut = [0 0;1 1.24];
powerFraction2FeedbackVoltLut = [0 0.192737;1 9.83176];
feedbackOffset_V = 0.174049;

% Calibration settings
calibrationNumPoints = 100;     % number of equidistant points to measure within the analog output range
calibrationAverageSamples = 20;     % per analog output voltage, average N analog input samples. This helps to reduce noise
calibrationNumRepeats = 10;     % number of times to repeat the calibration routine. the end result is the average of all calibration runs
calibrationSettlingTime_s = 0.001;     % pause between measurement points. this allows the beam modulation to settle
calibrationFlybackTime_s = 0.2;     % pause between calibration runs

% Advanced Settings. Note: these settings are unused for vDAQ based systems
modifiedLineClockIn = '';     % Terminal to which external beam trigger is connected. Leave empty for automatic routing via PXI/RTSI bus
frameClockIn = '';     % Terminal to which external frame clock is connected. Leave empty for automatic routing via PXI/RTSI bus
referenceClockIn = '';     % Terminal to which external reference clock is connected. Leave empty for automatic routing via PXI/RTSI bus
referenceClockRate = 1e+07;     % if referenceClockIn is used, referenceClockRate defines the rate of the reference clock in Hz. Default: 10e6Hz

minimizeOnStart = false;

widgetVisibility = 1;

%% dabs.generic.PMTAnalog (Green PMT)
AOGain = '/Dev2/AO0';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/AO0)
AOOutputRange = [0 0.9];     % <required if AOGain is defined> array of 1x2 numeric array specifying the minimum and maximum analog output voltage on the DAQ board that controls the PMT gain.
SupplyVoltageRange = [0 0.9];     % <required if AOGain is defined> array of 1x2 specifying the minimum and maximum for the PMT power supply in Volts.

DOPower = '';     % <optional> resource name of the digital output channel that switches the PMT on/off (e.g. /vDAQ0/D0.0)
DITripDetect = '';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/D0.1)
DOTripReset = '';     % <optional> resource name of the analog output channel that controls the PMT gain (e.g. /vDAQ0/D0.2)

% Calibration settings
wavelength_nm = 510;     % wavelength in nanometer
autoOn = true;     % powers the PMT automatically on for the duration of a scan
gain_V = 0.6;     % PMT power supply voltage

minimizeOnStart = false;

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
customFilterClockPeriod = 20;
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
auxTrigger3In = '/vDAQ0/D0.5';     % Digital input lines for aux trigger 3
auxTrigger4In = '';     % Digital input lines for aux trigger 4

% Signal Conditioning
disableMaskDivide = [false false];     % disable averaging of samples into pixels; instead accumulate samples
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
virtualChannelsSource = {'AI0' 'AI1'};
virtualChannelsMode = {'analog' 'analog'};
virtualChannelsThreshold = [false false];
virtualChannelsBinarize = [false false];
virtualChannelsEdgeDetect = [false false];
virtualChannelsLaserGate = [false false];
virtualChannelsDisableDivide = [false false];
virtualChannelsThresholdValue = [100 100];
virtualChannelsLaserFilterWindow = {[0 1] [0 1]};

%% scanimage.components.scan2d.RggScan (RGG_P2)

acquisitionDeviceId = 'vDAQ0';     % RDI Device ID
acquisitionEngineIdx = 1;

resonantScanner = 'X Reso P2';     % Name of the resonant scanner
xGalvo = 'X Galvo P2';     % Name of the x galvo scanner
yGalvo = 'Y Galvo P2';     % Name of the y galvo scanner
beams = {'920nm P2 Power' '1050nm P2 Power' '1035nm P2 Power'};     % beam device names
fastZs = {};     % fastZ device names
shutters = {'920nm P2' '1050nm P2' '1035nm P2'};     % shutter device names

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
auxTrigger3In = '/vDAQ0/D0.5';     % Digital input lines for aux trigger 3
auxTrigger4In = '';     % Digital input lines for aux trigger 4

% Signal Conditioning
disableMaskDivide = [false false];     % disable averaging of samples into pixels; instead accumulate samples
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
lineClockOut = '';     % Output line for the line clock
beamModifiedLineClockOut = '';     % Output line for beam clock
volumeTriggerOut = '';     % Output line for the volume clock

% Calibration data
scannerToRefTransform = [1.02621 0 6.63413;0 0.984738 5.30381;0 0 1];
LaserTriggerDebounceTicks = 1;
virtualChannelsSource = {'AI0' 'AI1'};
virtualChannelsMode = {'analog' 'analog'};
virtualChannelsThreshold = [false false];
virtualChannelsBinarize = [false false];
virtualChannelsEdgeDetect = [false false];
virtualChannelsLaserGate = [false false];
virtualChannelsDisableDivide = [false false];
virtualChannelsThresholdValue = [100 100];
virtualChannelsLaserFilterWindow = {[0 1] [0 1]};


%% dabs.zaber.ZaberMultiDevice (Zaber Motor)
comPort = 'COM3';     % Serial port the stage is connected to (e.g. 'COM3')
baudRate = 115200;     % Baudrate for serial communication
communicationProtocol = 'ASCII';     % Communication protocol ('ASCII' or 'Binary')
deviceLibraryPath = '';     % Path to '.sqlite' device library. Only required for offline use.
homingTimeout_s = 20;     % Timeout for homing move in seconds


minimizeOnStart = false;

%% dabs.micromanager.Camera (Basler Camera)
mmInstallDir = 'C:\Program Files\Micro-Manager-2.0';     % Path to MicroManager installation directory. e.g. 'C:\Program Files\Micro-Manager-1.4'
mmConfigFile = 'C:\Program Files\Micro-Manager-2.0\Basler_MMConfig.cfg';     % Path to MicroManager Device Config File. e.g. 'C:\Program Files\Micro-Manager-1.4\MMConfig_demo.cfg'

%% dabs.meadowlark.Slm1024v2 (slmv2)
PathFileLUT = 'C:\Program Files\Meadowlark Optics\Blink Plus\LUT Files\slm7116_at1064.lut';     % Absolute Path to the SLM look up table file. e.g. 'C:\pathToLutFile\myLut.lut' Note: this lut file is loaded directly to the hardware. The ScanImage software LUT is applied on top of the hardware LUT.
IndexBoard = 1;     % PCIe card index of the Meadowlark SLM

minimizeOnStart = false;

%% scanimage.components.scan2d.SlmScan (SLM)
slm = 'slmv2';     % name of the slm in use

linearScannerName = 'RGG_P2';     % Name of galvo-galvo-scanner (from first MDF section) to use in series with the SLM. Must be a linear scanner
deviceNameAcq = 'vDAQ0';     % String identifying NI DAQ board for PMT channels input

channelsInvert = [false false false false];     % Scalar or vector identifiying channels to invert. if scalar, the value is applied to all channels

shutters = {'1035nm P2'};     % shutter device names
beams = {'1035nm P2 Power'};     % Numeric: ID of the beam DAQ to use with the linear scan system

focalLength = 15;     % [mm] Focal length of the image forming lens of the SLM.
slmMediumRefractiveIdx = 1.0003;     % Refractive index of medium SLM works in. (typically air, 1.000293).
objectiveMediumRefractiveIdx = 1.003;     % Refractive index of medium objective works in. (typically water, 1.333).
zeroOrderBlockRadius = 1;     % [mm] Radius of area at center of SLM FOV that cannot be excited, usually due to presence of zero-order beam block
slmMagnificationOntoGalvos = 0.25;     % Magnification of SLM onto galvos. E.g. if SLM is demagnified onto galvo by a factor of 4, the value should be 0.25


% Calibration data
scannerToRefTransform = [1 0 0;0 1 0;0 0 1];
lutCalibrationAI = '';     % Name of AI channel for measuring zero order spot for LUT calibration


%% dabs.generic.WaveformGenerator (Turnaround Blanking P2 via P1 Sync)
taskType = 'Digital';
hControl = '/vDAQ0/D3.5';
hAIFeedback = '';
sampleRate_Hz = 20e6;          % or 20e6 if you want finer timing steps
startTriggerPort = '/vDAQ0/D3.4';
startTriggerEdge = 'falling'; % try this first
sampleMode = 'finite';
allowRetrigger = true;
wvfrmFcn = 'TurnaroundBlankClock';
amplitude = 0;
defaultValueVolts = 0;
periodSec = 0.1;              % ignored now, leave large
startDelay = 0;               % this is your only timing knob
dutyCycle = 50;
