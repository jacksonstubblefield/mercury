const { BACKEND, VENV_PY, run } = require("./lib.cjs");

run(VENV_PY, ["-m", "pytest", "-q", ...process.argv.slice(2)], { cwd: BACKEND });
