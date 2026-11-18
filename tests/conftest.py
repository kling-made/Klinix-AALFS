import subprocess
from pathlib import Path

import pytest

DOCTYPE = """<!DOCTYPE {tag} PUBLIC "-//OASIS//DTD DocBook XML V4.5//EN"
 "http://www.oasis-open.org/docbook/xml/4.5/docbookx.dtd" [
    <!ENTITY % general SYSTEM "{general}">
    %general;
]>"""
INCLUDE = '<xi:include xmlns:xi="http://www.w3.org/2001/XInclude" href="{}"/>'


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def xml(tag: str, inner: str, *, general: str = "../general.ent", attrs: str = "") -> str:
    return DOCTYPE.format(tag=tag, general=general) + f"<{tag}{attrs}>{inner}</{tag}>"


def write(path: Path, name: str, content: str) -> None:
    file = path / name
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(content, encoding="utf-8")


def section(name: str, commands: str, attrs: str = "") -> str:
    return xml(
        "sect1",
        f"""
        <sect1info condition="script">
          <productname>{name}</productname>
          <productnumber>&{name}-version;</productnumber>
          <address>&{name}-url;</address>
        </sect1info>
        <title>{name} &{name}-version;</title>
        <sect2 role="installation">{commands}</sect2>
        """,
        attrs=attrs,
    )


@pytest.fixture
def book_checkout(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "book"
    root.mkdir()
    git(root, "init", "--quiet")
    git(root, "config", "user.email", "tester@example.invalid")
    git(root, "config", "user.name", "Klinix Tests")
    write(root, "general.ent", """
      <!ENTITY % development "INCLUDE">
      <!ENTITY % release "IGNORE">
      <!ENTITY % rc "IGNORE">
      <!ENTITY % relnum "0.0">
      <!ENTITY % conditional SYSTEM "conditional.ent">
      %conditional;
      <![ %development; [
        <!ENTITY % version-entities SYSTEM "version.ent">
        %version-entities;
      ]]>
      <![ %release; [
        <!ENTITY version "%relnum;-systemd">
        <!ENTITY copyrightdate "1999-2026">
      ]]>
      <!ENTITY % packages SYSTEM "packages.ent">
      %packages;
      <!ENTITY % patches SYSTEM "patches.ent">
      %patches;
    """)
    write(root, "packages.ent", """
      <!ENTITY source-root "https://example.invalid/dist/">
      <!ENTITY core-version "1.2.3">
      <!ENTITY core-url "&source-root;core-&core-version;.tar.xz">
      <!ENTITY core-md5 "d41d8cd98f00b204e9800998ecf8427e">
      <!ENTITY systemd-version "42">
      <!ENTITY systemd-url "&source-root;systemd-&systemd-version;.tar.xz">
      <!ENTITY systemd-md5 "8d777f385d3dfec8815d20f7496026dc">
    """)
    write(root, "patches.ent", '<!ENTITY core-fix "core-&core-version;-fix.patch">')
    write(root, "index.xml", xml(
        "book",
        "<part>" + "".join(INCLUDE.format(f"chapter{i}/chapter{i}.xml") for i in ("03", "05", "08")) + "</part>",
        general="general.ent",
    ))
    write(root, "chapter03/chapter03.xml", "<chapter/>")
    packages = "".join(
        f"""
        <varlistentry>
          <term>{package} (&{package}-version;)</term>
          <listitem>
            <para>Download: <ulink url="&{package}-url;"/></para>
            <para>MD5 sum: <literal>&{package}-md5;</literal></para>
          </listitem>
        </varlistentry>
        """ for package in ("core", "systemd")
    )
    write(root, "chapter03/packages.xml", xml(
        "sect1", f'<variablelist role="materials">{packages}</variablelist>',
    ))
    write(root, "chapter03/patches.xml", xml("sect1", """
      <variablelist role="materials">
        <varlistentry>
          <term>Core fix</term>
          <listitem>
            <para>Download: <ulink url="&source-root;&core-fix;"/></para>
            <para>SHA-256: <literal>e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855</literal></para>
          </listitem>
        </varlistentry>
      </variablelist>
    """))
    write(root, "chapter05/chapter05.xml", xml("chapter", INCLUDE.format("core.xml")))
    write(root, "chapter05/core.xml", section("core", """
      <screen><userinput remap="pre">patch -i ../&core-fix;</userinput></screen>
      <screen><userinput remap="make">make</userinput></screen>
    """))
    write(root, "chapter08/chapter08.xml", xml(
        "chapter", "".join(INCLUDE.format(name) for name in ("core.xml", "other.xml", "systemd.xml")),
    ))
    write(root, "chapter08/core.xml", section("core", """
      <screen><userinput remap="make">make <replaceable>FLAGS</replaceable></userinput></screen>
      <screen revision="sysv"><userinput>must not be imported</userinput></screen>
    """))
    write(root, "chapter08/other.xml", section(
        "core", "<screen><userinput>other edition</userinput></screen>", attrs=' revision="sysv"',
    ))
    write(root, "chapter08/systemd.xml", section(
        "systemd",
        "<screen><userinput>printf '&version;&nbsp;&amp;'</userinput></screen>",
        attrs=' revision="systemd"',
    ))
    write(root, "appendices/mit-lic.xml", xml(
        "sect1", "<para>Copyright &copy; &copyrightdate; upstream test authors</para>",
    ))
    git(root, "add", ".")
    git(root, "commit", "-qm", "Test book source")
    git(root, "tag", "-am", "Test release", "r99.1")
    return root, git(root, "rev-parse", "HEAD")
