import os
import shutil
import subprocess
from pathlib import Path

import pytest

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


CALC = """<?php

namespace App;

class Calc
{
    public function adult(int $age): bool
    {
        return $age >= 18;
    }

    public function label(): string
    {
        return 1 > 0 ? 'yes' : 'no';
    }
}
"""

# A stand-in for vendor/bin/phpunit. Like the real one, it loads Composer
# through its own __DIR__, which PHP resolves through the worker's vendor
# link to the real project.
FAKE_PHPUNIT = """<?php
require __DIR__ . '/../autoload.php';
foreach (array_slice($argv, 1) as $file) {
    require $file;
}
"""

CALC_TEST = """<?php
use App\\Calc;

if (! (new Calc())->adult(18) || (new Calc())->adult(17)) {
    fwrite(STDERR, "adult() is wrong\\n");
    exit(1);
}
"""

needs_php = pytest.mark.skipif(
    shutil.which("php") is None or shutil.which("composer") is None,
    reason="needs php and composer on PATH",
)


def _composer_project(root: Path) -> Path:
    _write(root, "composer.json", '{"autoload": {"psr-4": {"App\\\\": "app/"}}}')
    source = _write(root, "app/Calc.php", CALC)
    _write(root, "tests/CalcTest.php", CALC_TEST)
    _write(root, ".env", "SECRET=keep-me\n")
    environment = {**os.environ, "COMPOSER_DISABLE_NETWORK": "1"}
    subprocess.run(
        ["composer", "dump-autoload", "--optimize", "--no-interaction", "--quiet"],
        cwd=root,
        env=environment,
        check=True,
    )
    _write(root, "vendor/bin/phpunit", FAKE_PHPUNIT)
    return source


def _mutate(source: Path, root: Path):
    from mutator.engine import mutate_file
    from mutator.runner import CommandRunner

    return mutate_file(
        source,
        root,
        runner=CommandRunner(),
        covered_lines=None,
        ignore_coverage=True,
        mutate_all=True,
        lines=None,
        test_command=None,
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
        max_workers=2,
    )


def _outcomes(result) -> dict[tuple[str, str, str], str]:
    return {
        (site.name, site.original, site.mutant): result.statuses[site.mutation_id] for site in result.sites
    }


@needs_php
def test_a_php_mutant_runs_the_worker_copy_not_the_composer_classmap(tmp_path):
    source = _composer_project(tmp_path)
    original = source.read_bytes()
    assert "app/Calc.php" in (tmp_path / "vendor/composer/autoload_classmap.php").read_text()
    outcomes = _outcomes(_mutate(source, tmp_path))
    assert outcomes[("adult", ">=", ">")] == "killed"
    assert outcomes[("label", ">", ">=")] == "survived"
    assert source.read_bytes() == original
    assert list((tmp_path / "target" / "mutation-workers").iterdir()) == []
    assert not (tmp_path / ".mutator-php").exists()
    assert (tmp_path / ".env").read_text() == "SECRET=keep-me\n"


@needs_php
def test_without_the_prepend_composer_loads_the_original_and_the_mutant_survives(tmp_path, monkeypatch):
    from mutator import php

    source = _composer_project(tmp_path)
    monkeypatch.setattr(php, "worker_command", lambda command, worker: command)
    outcomes = _outcomes(_mutate(source, tmp_path))
    assert outcomes[("adult", ">=", ">")] == "survived"


def test_the_prepend_maps_each_class_to_the_worker_copy(tmp_path):
    from mutator.php import prepend_text

    text = prepend_text(Path("/p/it's/vendor/autoload.php"), ["App\\Models\\Coupon"], Path("/w/app/Coupon.php"))
    assert text == (
        "<?php\n"
        "$mutatorLoader = require '/p/it\\'s/vendor/autoload.php';\n"
        "$mutatorLoader->addClassMap(['App\\\\Models\\\\Coupon' => '/w/app/Coupon.php']);\n"
        "unset($mutatorLoader);\n"
    )


def test_the_mutant_command_carries_the_prepend(tmp_path, capsys):
    from mutator.php import warn_override, worker_command

    command = ["env", "XDEBUG_MODE=off", "php", "vendor/bin/pest", "tests/ATest.php"]
    assert worker_command(command, tmp_path) == command
    prepend = _write(tmp_path, ".mutator-php/prepend.php", "<?php\n").resolve()
    assert worker_command(command, tmp_path) == [
        "env",
        "XDEBUG_MODE=off",
        "php",
        "-d",
        f"auto_prepend_file={prepend}",
        "vendor/bin/pest",
        "tests/ATest.php",
    ]
    typed = 'php -d auto_prepend_file="$MUTATOR_PHP_PREPEND" vendor/bin/pest'
    assert worker_command(typed, tmp_path) == f"export MUTATOR_PHP_PREPEND={prepend}; {typed}"
    warn_override(typed)
    assert capsys.readouterr().err == ""
    warn_override("vendor/bin/pest")
    assert "MUTATOR_PHP_PREPEND" in capsys.readouterr().err
