from mutator.kotlin_version import before_1_9, project_version


def _source(tmp_path):
    source = tmp_path / "app" / "src" / "main" / "kotlin" / "demo" / "Calc.kt"
    source.parent.mkdir(parents=True)
    source.write_text("package demo\n", encoding="utf-8")
    return source


def test_reads_the_kotlin_plugin_version(tmp_path):
    source = _source(tmp_path)
    (tmp_path / "app" / "build.gradle.kts").write_text(
        'plugins {\n    kotlin("jvm") version "1.8.22"\n}\n', encoding="utf-8"
    )
    assert project_version(source, tmp_path) == (1, 8)
    assert before_1_9(source, tmp_path)


def test_each_build_file_spelling_names_a_version(tmp_path):
    spellings = {
        "build.gradle": "plugins { id 'org.jetbrains.kotlin.jvm' version '1.7.10' }",
        "gradle.properties": "kotlin_version=1.9.0\n",
        "pom.xml": "<properties><kotlin.version>2.0.21</kotlin.version></properties>",
        "settings.gradle.kts": 'classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:1.6.21")',
    }
    expected = {"build.gradle": (1, 7), "gradle.properties": (1, 9), "pom.xml": (2, 0), "settings.gradle.kts": (1, 6)}
    for name, text in spellings.items():
        root = tmp_path / name
        source = _source(root)
        (root / name).write_text(text, encoding="utf-8")
        assert project_version(source, root) == expected[name], name


def test_reads_a_version_catalog_and_maven_plugin(tmp_path):
    source = _source(tmp_path)
    catalog = tmp_path / "gradle" / "libs.versions.toml"
    catalog.parent.mkdir()
    catalog.write_text('[versions]\nktor = "2.3.0"\nkotlin = "1.8.0"\n', encoding="utf-8")
    assert project_version(source, tmp_path) == (1, 8)

    maven = tmp_path / "maven"
    maven_source = _source(maven)
    (maven / "pom.xml").write_text(
        "<plugin><artifactId>kotlin-maven-plugin</artifactId>\n  <version>1.9.24</version></plugin>",
        encoding="utf-8",
    )
    assert project_version(maven_source, maven) == (1, 9)
    assert not before_1_9(maven_source, maven)


def test_an_unknown_version_counts_as_new(tmp_path):
    source = _source(tmp_path)
    (tmp_path / "build.gradle.kts").write_text("plugins { java }\n", encoding="utf-8")
    assert project_version(source, tmp_path) is None
    assert not before_1_9(source, tmp_path)
