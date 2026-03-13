"""Unit tests for db_config_parser. No database required."""
import configparser
import os
import tempfile

import pytest

import db_config_parser


@pytest.mark.unit
class TestParseInfo:
    def _make_ini(self, sections: dict) -> str:
        cfg = configparser.ConfigParser()
        for section, values in sections.items():
            cfg[section] = values
        path = os.path.join(tempfile.gettempdir(), "test_parse_info.ini")
        with open(path, "w") as f:
            cfg.write(f)
        return path

    def test_parse_info_returns_section_dict(self):
        path = self._make_ini({"postgresql": {"host": "myhost", "database": "mydb"}})
        result = db_config_parser.parse_info(path, "postgresql")
        assert result["host"] == "myhost"
        assert result["database"] == "mydb"

    def test_parse_info_missing_section_raises(self):
        path = self._make_ini({"postgresql": {"host": "myhost"}})
        with pytest.raises(Exception, match="Section redis not found"):
            db_config_parser.parse_info(path, "redis")


@pytest.mark.unit
class TestGetConnStr:
    def test_builds_connection_string(self, monkeypatch, tmp_path):
        ini = tmp_path / "test.ini"
        ini.write_text(
            "[postgresql]\nhost=h\ndatabase=d\nuser=u\npassword=p\nport=5432\n"
        )
        monkeypatch.setenv("DB_CONFIG", str(ini))
        result = db_config_parser.get_conn_str()
        assert result == "postgresql://u:p@h:5432/d"

    def test_uses_db_config_env_var(self, monkeypatch, tmp_path):
        ini = tmp_path / "custom.ini"
        ini.write_text(
            "[postgresql]\nhost=custom\ndatabase=cdb\nuser=cu\npassword=cp\nport=9999\n"
        )
        monkeypatch.setenv("DB_CONFIG", str(ini))
        conn = db_config_parser.get_conn_str()
        assert "custom" in conn
        assert "cdb" in conn
        assert "9999" in conn


@pytest.mark.unit
class TestSecurityConfig:
    def test_security_api_key(self, monkeypatch, tmp_path):
        ini = tmp_path / "sec.ini"
        ini.write_text(
            "[postgresql]\nhost=h\ndatabase=d\nuser=u\npassword=p\nport=5432\n"
            "[security]\napikey=mysecretkey\napiversion=v3\napiport=8080\nxiviewbaseurl=https://x/\n"
        )
        monkeypatch.setenv("DB_CONFIG", str(ini))
        assert db_config_parser.security_API_key() == "mysecretkey"

    def test_api_version(self, monkeypatch, tmp_path):
        ini = tmp_path / "ver.ini"
        ini.write_text(
            "[postgresql]\nhost=h\ndatabase=d\nuser=u\npassword=p\nport=5432\n"
            "[security]\napikey=k\napiversion=v2\napiport=8080\nxiviewbaseurl=https://x/\n"
        )
        monkeypatch.setenv("DB_CONFIG", str(ini))
        assert db_config_parser.API_version() == "v2"

    def test_api_port(self, monkeypatch, tmp_path):
        ini = tmp_path / "port.ini"
        ini.write_text(
            "[postgresql]\nhost=h\ndatabase=d\nuser=u\npassword=p\nport=5432\n"
            "[security]\napikey=k\napiversion=v2\napiport=9090\nxiviewbaseurl=https://x/\n"
        )
        monkeypatch.setenv("DB_CONFIG", str(ini))
        assert db_config_parser.API_port() == "9090"


@pytest.mark.unit
class TestRedisConfig:
    def test_redis_config_returns_dict(self, monkeypatch, tmp_path):
        ini = tmp_path / "redis.ini"
        ini.write_text(
            "[postgresql]\nhost=h\ndatabase=d\nuser=u\npassword=p\nport=5432\n"
            "[redis]\nhost=redishost\nport=6379\npassword=rpass\npeptide_per_protein=ppp_key\n"
        )
        monkeypatch.setenv("DB_CONFIG", str(ini))
        cfg = db_config_parser.redis_config()
        assert cfg["host"] == "redishost"
        assert cfg["port"] == "6379"

    def test_redis_config_missing_section_raises(self, monkeypatch, tmp_path):
        ini = tmp_path / "noredis.ini"
        ini.write_text(
            "[postgresql]\nhost=h\ndatabase=d\nuser=u\npassword=p\nport=5432\n"
        )
        monkeypatch.setenv("DB_CONFIG", str(ini))
        with pytest.raises(Exception):
            db_config_parser.redis_config()
