"""M15 tests (SPEC 015-deploy-kit): deploy files structural checks (AE1-AE2)."""
import os
import re

DEPLOY = os.path.join(os.path.dirname(__file__), "../../deploy")


def read(name):
    with open(os.path.join(DEPLOY, name), encoding="utf-8") as fh:
        return fh.read()


class TestFilesExist:
    def test_all_four_files(self):
        for name in ("lifesim.service", "Caddyfile", ".env.example",
                     "README.md"):
            assert os.path.isfile(os.path.join(DEPLOY, name)), name
            assert len(read(name)) > 50, name

    def test_run_entrypoint_exists(self):
        path = os.path.join(
            os.path.dirname(__file__), "../../backend/app/run.py")
        assert os.path.isfile(path)


class TestSystemdUnit:
    def test_required_keys(self):
        unit = read("lifesim.service")
        for key in ("[Unit]", "[Service]", "[Install]",
                    "User=lifesim", "WorkingDirectory=/opt/lifesim",
                    "EnvironmentFile=/opt/lifesim/.env",
                    "Restart=on-failure",
                    "ExecStart="):
            assert key in unit, key

    def test_entrypoint_and_port(self):
        unit = read("lifesim.service")
        assert "app.run:app" in unit
        assert "--port 8000" in unit
        assert "127.0.0.1" in unit  # not publicly bound


class TestCaddyfile:
    def test_proxy_target(self):
        caddy = read("Caddyfile")
        assert "reverse_proxy 127.0.0.1:8000" in caddy
        # placeholder domain must be replaced by operator (documented)
        assert "example.com" in caddy


class TestEnvExample:
    def test_keystone_flags_safe(self):
        env = read(".env.example")
        # П2: all experimental flags off / deterministic
        assert "weather__source=synthetic" in env
        assert "fire__spontaneous_chance_per_day=0.0" in env
        assert "external__npc_utility=false" in env
        assert "external__supply_demand=false" in env
        assert "visual__enabled=false" in env
        assert "llm__enabled=false" in env

    def test_no_real_secrets(self):
        env = read(".env.example")
        # AE2: placeholder only, no hex-looking secret
        assert "VL1_SECRET=" in env
        hexvals = re.findall(r"VL1_SECRET=([0-9a-fA-F]{32,})", env)
        assert hexvals == []
        assert "openssl rand -hex 32" in env  # generation command documented

    def test_paths_consistent(self):
        env = read(".env.example")
        unit = read("lifesim.service")
        assert "/opt/lifesim" in unit
        assert "LIFESIM_CONFIG=config/default.yaml" in env


class TestRunbook:
    def test_sections_covered(self):
        rb = read("README.md")
        for section in ("Установка", ".env", "systemd", "Caddy",
                        "Проверка", "Smoke-тест реальной погоды",
                        "Бэкап SQLite", "Обновление"):
            assert section in rb, section

    def test_weather_smoke_mentions_reineke(self):
        rb = read("README.md")
        assert "Рейнеке" in rb
        assert "historical" in rb
        assert "weather_cache" in rb
