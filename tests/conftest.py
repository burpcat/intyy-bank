import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest

VAR = Path(tempfile.mkdtemp(prefix="kvfcu-test-"))
SECRET = "test-secret"
CREDS = {"teller": ("teller1", "tellerpw"), "supervisor": ("super1", "superpw"),
         "restricted": ("viewer1", "viewerpw")}
os.environ.update({"KVFCU_VAR_DIR": str(VAR), "KVFCU_PROXY_SECRET": SECRET})
for role, (u, p) in CREDS.items():
    os.environ[f"KVFCU_{role.upper()}_USER"], os.environ[f"KVFCU_{role.upper()}_PASS"] = u, p

from seed import seed  # noqa: E402

seed.build(VAR)
from bank import app as bank  # noqa: E402

VALID, AT_LIMIT = seed.member_numbers()


@pytest.fixture
def client():
    shutil.copyfile(VAR / "seed.db", VAR / "live.db")
    c = bank.app.test_client()
    c.environ_base["HTTP_X_KVFCU_PROXY"] = SECRET
    return c


@pytest.fixture
def login(client):
    def _login(role="teller"):
        u, p = CREDS[role]
        assert client.post("/login.do", data={"userId": u, "pwd": p}).status_code == 302
        return client
    return _login


def seq(html):
    return re.search(r'name="pageSeq" value="(\d+)"', html).group(1)
