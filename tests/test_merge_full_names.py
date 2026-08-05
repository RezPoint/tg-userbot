"""Standalone-тест merge_full_names: python3 tests/test_merge_full_names.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from userbot.scam_ingest import extract, merge_full_names  # noqa: E402


def eq(got, exp, msg):
    assert got == exp, f"{msg}: got {got!r}, want {exp!r}"


def test_adds_informal_name_regex_missed():
    text = "т- банк НИКОЛАЙ Б. залился полностью, не берите"
    eq(merge_full_names([], ["Николай Б."], text), ["Николай Б."], "informal name added")


def test_dedup_case_insensitive():
    text = "ФИО: Кристина Андреевна Лелюх"
    eq(merge_full_names(["Кристина Андреевна Лелюх"],
                        ["кристина андреевна лелюх"], text),
       ["Кристина Андреевна Лелюх"], "dup skipped")


def test_blocks_hallucination_not_in_text():
    text = "т- банк НИКОЛАЙ Б. залился"
    eq(merge_full_names([], ["Пётр Сидоров"], text), [], "name absent from text blocked")


def test_keeps_existing_and_appends():
    text = "Иван Иванов и Пётр Петров кидалы"
    eq(merge_full_names(["Иван Иванов"], ["Пётр Петров"], text),
       ["Иван Иванов", "Пётр Петров"], "append new")


def test_empty_llm():
    eq(merge_full_names(["Иван Иванов"], [], "Иван Иванов"), ["Иван Иванов"], "empty llm")


def test_email_domain_is_not_telegram_username():
    result = extract("Почта: glebaskonev@yandex.ru и ba***0@yandex.ru")
    eq(result.usernames, [], "email domain skipped")


def test_real_telegram_username_is_kept():
    result = extract("Телеграм: @real_user")
    eq(result.usernames, ["real_user"], "telegram username kept")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
    print(f"OK: {len(fns)} tests passed")
