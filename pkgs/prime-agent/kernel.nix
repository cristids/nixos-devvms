{ pkgs }:
let
  prime = pkgs.callPackage ./default.nix { };
  python = pkgs.python3.override {
    packageOverrides = final: prev: {
      mcp = final.buildPythonPackage {
        pname = "mcp";
        version = "2.2.0";
        pyproject = true;
        src = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/76/31/ac54fb0fdd5b37de704486e288bba4fbbb463f24cfcfedbede407b854513/mcp-2.2.0.tar.gz";
          sha256 = "2dc37ecb1974becdcebdbf7561e7c15a07dbbf20ba21ba16c3593b3038b3afbd";
        };
        build-system = with final; [ hatchling uv-dynamic-versioning ];
        dependencies = with final; [ anyio httpx2 jsonschema mcp-types opentelemetry-api pydantic pyjwt cryptography python-multipart sse-starlette starlette typing-extensions typing-inspection uvicorn ];
        doCheck = false;
      };
      pyjwt = final.buildPythonPackage {
        pname = "pyjwt";
        version = "2.15.0";
        pyproject = true;
        src = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/02/a5/5197bfd06417837ac079921c66fa6393f1dea3557272a263cebfef69e432/pyjwt-2.15.0.tar.gz";
          sha256 = "b11c5f9791d7bf51c2b39a81ed669f6b2dbbd669df2942f6c60167e9e3d1abe4";
        };
        build-system = with final; [ setuptools ];
        dependencies = with final; [ cryptography ];
        doCheck = false;
      };
      opentelemetry-api = final.buildPythonPackage {
        pname = "opentelemetry-api";
        version = "1.45.1";
        pyproject = true;
        src = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/2e/02/6e0ae9cc61bd3169d401077b507b3ebc344745171e1051ab430be012dcd9/opentelemetry_api-1.45.1.tar.gz";
          sha256 = "aa38ed19bcc084ba42782a73255b3582283eced7ad6dddbd6695189e69adfb75";
        };
        build-system = with final; [ hatchling ];
        dependencies = with final; [ typing-extensions ];
        doCheck = false;
      };
      mcp-types = final.buildPythonPackage {
        pname = "mcp-types";
        version = "2.2.0";
        pyproject = true;
        src = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/ae/91/762d7755d971aff8a28d75f7961656148edf27875c8026e6385aaab08ae7/mcp_types-2.2.0.tar.gz";
          sha256 = "d3ed53703ddd10d9c6399f29d322bb66f3f67ab41348ac8556ba23e07fedefad";
        };
        build-system = with final; [ hatchling uv-dynamic-versioning ];
        dependencies = with final; [ pydantic typing-extensions ];
        doCheck = false;
      };
      httpx2 = final.buildPythonPackage {
        pname = "httpx2";
        version = "2.5.0";
        pyproject = true;
        src = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/d0/e2/b5dedc0cf35aa65de5f541ccd30d2bc1fd7f1d43c9ab09f8ed9a7342317b/httpx2-2.5.0.tar.gz";
          sha256 = "e2df9cb4611021527ff8a675b1c320b610a2ec397acc8d6fe6e91df2d9b33c29";
        };
        build-system = with final; [ hatchling hatch-fancy-pypi-readme uv-dynamic-versioning ];
        dependencies = with final; [ anyio httpcore2 idna truststore ];
        doCheck = false;
      };
      httpcore2 = final.buildPythonPackage {
        pname = "httpcore2";
        version = "2.5.0";
        pyproject = true;
        src = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/47/06/5c12df521b5322fb1114a83d46911b2fbcb8855ddb3a635f11c01a214af5/httpcore2-2.5.0.tar.gz";
          sha256 = "88aa170137c17328d5ac44234f9fd10706466d5fb347f3edac4d39b91137b09d";
        };
        build-system = with final; [ hatchling hatch-fancy-pypi-readme uv-dynamic-versioning ];
        dependencies = with final; [ h11 truststore ];
        doCheck = false;
      };
      idna = final.buildPythonPackage {
        pname = "idna";
        version = "3.18";
        pyproject = true;
        src = pkgs.fetchurl {
          url = "https://files.pythonhosted.org/packages/cd/63/9496c57188a2ee585e0f1db071d75089a11e98aa86eb99d9d7618fc1edce/idna-3.18.tar.gz";
          sha256 = "ffb385a7e039654cef1ab9ef32c6fafe283c0c0467bba1d9029738ce4a14a848";
        };
        build-system = with final; [ flit-core ];
        dependencies = with final; [  ];
        doCheck = false;
      };
      prime-skill-agent-message = final.buildPythonPackage {
        pname = "prime-skill-agent-message";
        version = "0.1.0";
        pyproject = true;
        src = prime.src;
        unpackPhase = ''
          mkdir source
          tar -xzf $src -C source
          sourceRoot=source/skills/agent-message
        '';
        build-system = [ final.hatchling ];
        dependencies = [ final.prime-agent-runtime final.pillow ];
        pythonImportsCheck = [ "agent_message" ];
        doCheck = false;
      };
      prime-skill-attach-image = final.buildPythonPackage {
        pname = "prime-skill-attach-image";
        version = "0.1.0";
        pyproject = true;
        src = prime.src;
        unpackPhase = ''
          mkdir source
          tar -xzf $src -C source
          sourceRoot=source/skills/attach-image
        '';
        build-system = [ final.hatchling ];
        dependencies = [ final.prime-agent-runtime final.pillow ];
        pythonImportsCheck = [ "attach_image" ];
        doCheck = false;
      };
      prime-skill-compact = final.buildPythonPackage {
        pname = "prime-skill-compact";
        version = "0.1.0";
        pyproject = true;
        src = prime.src;
        unpackPhase = ''
          mkdir source
          tar -xzf $src -C source
          sourceRoot=source/skills/compact
        '';
        build-system = [ final.hatchling ];
        dependencies = [ final.prime-agent-runtime final.pillow ];
        pythonImportsCheck = [ "compact" ];
        doCheck = false;
      };
      prime-skill-edit = final.buildPythonPackage {
        pname = "prime-skill-edit";
        version = "0.1.0";
        pyproject = true;
        src = prime.src;
        unpackPhase = ''
          mkdir source
          tar -xzf $src -C source
          sourceRoot=source/skills/edit
        '';
        build-system = [ final.hatchling ];
        dependencies = [ final.prime-agent-runtime final.pillow ];
        pythonImportsCheck = [ "edit" ];
        doCheck = false;
      };
      prime-skill-goal = final.buildPythonPackage {
        pname = "prime-skill-goal";
        version = "0.1.0";
        pyproject = true;
        src = prime.src;
        unpackPhase = ''
          mkdir source
          tar -xzf $src -C source
          sourceRoot=source/skills/goal
        '';
        build-system = [ final.hatchling ];
        dependencies = [ final.prime-agent-runtime final.pillow ];
        pythonImportsCheck = [ "goal" ];
        doCheck = false;
      };
      prime-skill-refine = final.buildPythonPackage {
        pname = "prime-skill-refine";
        version = "0.1.0";
        pyproject = true;
        src = prime.src;
        unpackPhase = ''
          mkdir source
          tar -xzf $src -C source
          sourceRoot=source/skills/refine
        '';
        build-system = [ final.hatchling ];
        dependencies = [ final.prime-agent-runtime final.pillow ];
        pythonImportsCheck = [ "refine" ];
        doCheck = false;
      };
      prime-agent-runtime = final.buildPythonPackage {
        pname = "prime-agent-runtime";
        version = "0.1.0";
        pyproject = true;
        src = prime.src;
        unpackPhase = ''
          mkdir source
          tar -xzf $src -C source
          sourceRoot=source/prime-agent-runtime
        '';
        build-system = [ final.hatchling ];
        dependencies = with final; [ mcp pyjwt opentelemetry-api tyro ];
        pythonImportsCheck = [ "rlm" "rlm.repl" ];
        doCheck = false;
      };
    };
  };
in python.withPackages (p: with p; [
  prime-skill-agent-message prime-skill-attach-image prime-skill-compact
  prime-skill-edit prime-skill-goal prime-skill-refine
  prime-agent-runtime dill requests httpx pyyaml tomli python-dotenv pandas numpy
  scipy beautifulsoup4 lxml pydantic tyro
])
