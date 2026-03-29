function schema = loadSchemaYaml(schemaPath, pythonExecutable)
arguments
    schemaPath (1,1) string
    pythonExecutable (1,1) string = "python"
end

schemaPath = string(schemaPath);
pythonExecutable = string(pythonExecutable);

if ~isfile(schemaPath)
    error('Schema file not found: %s', schemaPath);
end

repoRoot = fileparts(fileparts(fileparts(fileparts(mfilename('fullpath')))));
helperPath = fullfile(repoRoot, 'scripts', 'schema_to_json.py');

if ~isfile(helperPath)
    error('Schema helper script not found: %s', helperPath);
end

command = strjoin([
    quoteForShell(pythonExecutable)
    quoteForShell(helperPath)
    quoteForShell(schemaPath)
], " ");

disp('loadSchemaYaml: running helper command');
disp(command);
[status, output] = system(command);
disp('loadSchemaYaml: helper command completed');
disp(status);
if status ~= 0
    error('Failed to load schema YAML via helper script.\nCommand: %s\nOutput:\n%s', command, output);
end

schema = jsondecode(output);


function quoted = quoteForShell(value)
value = string(value);
quoted = '"' + replace(value, '"', '\"') + '"';
end
end
