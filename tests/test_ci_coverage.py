"""打包前的 JUnit 门禁必须拒绝缺测、跳过、失败和错误。"""
import xml.etree.ElementTree as ET

import pytest

from tools.check_lifecycle_results import REQUIRED_TESTS, check_report


@pytest.fixture
def report(tmp_path):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for module, names in REQUIRED_TESTS.items():
        for name in sorted(names):
            ET.SubElement(suite, "testcase", classname=f"tests.{module}", name=name)
    path = tmp_path / "results.xml"
    return ET.ElementTree(root), suite, path


def test_ci_accepts_complete_successful_coverage(report):
    tree, _, path = report
    tree.write(path)
    assert not check_report(path)


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error"])
def test_ci_rejects_unsuccessful_required_coverage(report, outcome):
    tree, suite, path = report
    case = list(suite)[0]
    ET.SubElement(case, outcome, message="deliberate regression")
    tree.write(path)
    assert any(error.startswith(outcome + ":") for error in check_report(path))


def test_ci_rejects_missing_test(report):
    tree, suite, path = report
    suite.remove(list(suite)[0])
    tree.write(path)
    assert any(error.startswith("Missing required test:") for error in check_report(path))


def test_ci_rejects_empty_report(tmp_path):
    path = tmp_path / "empty.xml"
    path.write_text("<testsuites/>", encoding="utf-8")
    assert check_report(path)
