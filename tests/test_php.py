from pathlib import Path

from mutator.php import declared_classes, naming_tests
from mutator.runner import test_plan as plan_command

COUPON = """<?php

namespace App\\Models;

use Illuminate\\Database\\Eloquent\\Model;

class Coupon extends Model
{
    public function isActive(): bool
    {
        return $this->uses < 10;
    }
}
"""


def _write(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _project(root: Path) -> Path:
    _write(root, "composer.json", "{}")
    return _write(root, "app/Models/Coupon.php", COUPON)


def test_declared_classes_are_fully_qualified():
    source = COUPON + "\ntrait Usable {}\nenum Kind {}\ninterface Shape {}\n$x = new class {};\n"
    assert declared_classes(source) == [
        "App\\Models\\Coupon",
        "App\\Models\\Usable",
        "App\\Models\\Kind",
        "App\\Models\\Shape",
    ]
    assert declared_classes("<?php\nclass Bare {}\n") == ["Bare"]


def test_tests_that_name_the_class_are_selected(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/Unit/ImportTest.php", "<?php\nuse App\\Models\\Coupon;\n")
    _write(tmp_path, "tests/Unit/AliasTest.php", "<?php\nuse App\\Models\\Coupon as Promo;\n")
    _write(tmp_path, "tests/Feature/QualifiedTest.php", "<?php\nit('x', fn () => \\App\\Models\\Coupon::query());\n")
    _write(tmp_path, "tests/Feature/GroupedTest.php", "<?php\nuse App\\Models\\{\n    User,\n    Coupon,\n};\n")
    _write(tmp_path, "tests/Feature/PrefixGroupTest.php", "<?php\nuse App\\{Models\\Coupon as C, Enums\\Kind};\n")
    assert naming_tests(tmp_path, ["App\\Models\\Coupon"]) == [
        "tests/Feature/GroupedTest.php",
        "tests/Feature/PrefixGroupTest.php",
        "tests/Feature/QualifiedTest.php",
        "tests/Unit/AliasTest.php",
        "tests/Unit/ImportTest.php",
    ]


def test_decoys_and_helpers_are_not_selected(tmp_path):
    _project(tmp_path)
    _write(tmp_path, "tests/Unit/UsageTest.php", "<?php\nuse App\\Models\\CouponUsage;\n")
    _write(tmp_path, "tests/Unit/NestedTest.php", "<?php\nuse App\\Models\\Coupon\\Code;\n")
    _write(tmp_path, "tests/Unit/OtherGroupTest.php", "<?php\nuse App\\Models\\{CouponUsage, User};\nCoupon::query();\n")
    _write(tmp_path, "tests/Unit/ShortNameTest.php", "<?php\nCoupon::query();\n")
    _write(tmp_path, "tests/Helpers/Functions.php", "<?php\nuse App\\Models\\Coupon;\n")
    _write(tmp_path, "tests/Pest.php", "<?php\nuse App\\Models\\Coupon;\n")
    _write(tmp_path, "app/Http/CouponControllerTest.php", "<?php\nuse App\\Models\\Coupon;\n")
    assert naming_tests(tmp_path, ["App\\Models\\Coupon"]) == []


def test_the_php_plan_runs_the_naming_tests_in_process(tmp_path):
    source = _project(tmp_path)
    _write(tmp_path, "tests/Unit/ImportTest.php", "<?php\nuse App\\Models\\Coupon;\n")
    _write(tmp_path, "tests/Unit/BTest.php", "<?php\nuse App\\Models\\Coupon;\n")
    _write(tmp_path, "vendor/bin/phpunit", "<?php\n")
    command, directory = plan_command(tmp_path, source, "php", None)
    assert directory == tmp_path.resolve()
    assert command == [
        "env",
        "XDEBUG_MODE=off",
        "php",
        "vendor/bin/phpunit",
        "tests/Unit/BTest.php",
        "tests/Unit/ImportTest.php",
    ]
    _write(tmp_path, "vendor/bin/pest", "<?php\n")
    _write(tmp_path, "artisan", "<?php\n")
    command, _directory = plan_command(tmp_path, source, "php", None)
    assert command[:4] == ["env", "XDEBUG_MODE=off", "php", "vendor/bin/pest"]


def test_a_php_file_no_test_names_has_no_command(tmp_path):
    source = _project(tmp_path)
    _write(tmp_path, "tests/Unit/OtherTest.php", "<?php\nuse App\\Models\\User;\n")
    command, directory = plan_command(tmp_path, source, "php", None)
    assert command is None
    assert directory == tmp_path.resolve()
    override, _directory = plan_command(tmp_path, source, "php", "vendor/bin/pest")
    assert override == "vendor/bin/pest"


class CountingRunner:
    verbose = False

    def __init__(self):
        self.calls = 0

    def run(self, command, cwd, timeout):
        self.calls += 1
        raise AssertionError("no test names the class, so nothing should run")


def test_sites_of_a_class_no_test_names_are_uncovered_and_not_run(tmp_path, capsys):
    from mutator.engine import mutate_file

    source = _project(tmp_path)
    runner = CountingRunner()
    result = mutate_file(
        source,
        tmp_path,
        runner=runner,
        covered_lines=None,
        ignore_coverage=True,
        mutate_all=True,
        lines=None,
        test_command=None,
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
    )
    assert runner.calls == 0
    form = result.forms[0]
    assert (form.id, form.sites, form.uncovered, form.killed, form.survived) == ("defn/isActive", 1, 1, 0, 0)
    assert set(result.statuses.values()) == {"uncovered"}
    assert "No test under tests/ names App\\Models\\Coupon" in capsys.readouterr().err
