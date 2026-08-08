#!/usr/bin/env python3
"""
test_next_task.py - stdlib unittest suite for next_task.py.

    python -m unittest test_next_task -v

Every test runs against a throwaway store under the system temp directory.
Nothing here ever touches personal/ or work/ - those hold real data, including
real company governance content.

Stdlib only, same rule as next_task.py itself.
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import next_task

REPO_ROOT = Path(__file__).resolve().parent


def run_cli(store, *args, cwd=None):
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "next_task.py"), "--store", str(store), *args],
        capture_output=True, text=True, cwd=cwd,
    )


class StoreTestCase(unittest.TestCase):
    """Base: a fresh empty store per test, removed afterwards."""

    def setUp(self):
        self.store = Path(tempfile.mkdtemp(prefix="tickettest_"))
        self.tickets_dir = self.store / "tickets"
        self.tickets_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.store, ignore_errors=True)

    def make_ticket(self, tid="TEST-001", **overrides):
        ticket = {
            "id": tid,
            "title": "a ticket",
            "description": "",
            "status": "open",
            "priority": "medium",
            "tags": [],
            "depends_on": [],
            "created_at": next_task.now_iso(),
            "updated_at": next_task.now_iso(),
        }
        ticket.update(overrides)
        next_task.save_ticket(self.store, ticket)
        return ticket

    def run_cli(self, *args):
        return run_cli(self.store, *args)


class StoreValidationTest(unittest.TestCase):
    """A mistyped --store must fail loudly, not quietly invent an empty store.

    The personal/work split is a compliance boundary, so "which store is this"
    has to be answerable by the path. That only holds if a wrong path stops."""

    def setUp(self):
        self.parent = Path(tempfile.mkdtemp(prefix="tickettest_"))

    def tearDown(self):
        shutil.rmtree(self.parent, ignore_errors=True)

    def test_a_command_against_a_missing_folder_fails(self):
        missing = self.parent / "wrok"

        proc = run_cli(missing, "list")

        self.assertNotEqual(proc.returncode, 0, f"succeeded anyway: {proc.stdout}")
        self.assertFalse(missing.exists(), "the mistyped path was created regardless")

    def test_the_error_says_which_path_and_how_to_create_it(self):
        missing = self.parent / "wrok"

        proc = run_cli(missing, "list")

        self.assertIn(str(missing), proc.stderr)
        self.assertIn("init", proc.stderr)

    def test_a_folder_with_no_tickets_subfolder_is_not_a_store(self):
        not_a_store = self.parent / "somewhere-else"
        not_a_store.mkdir()

        proc = run_cli(not_a_store, "list")

        self.assertNotEqual(proc.returncode, 0, f"succeeded anyway: {proc.stdout}")

    def test_init_creates_a_store_that_then_works(self):
        fresh = self.parent / "fresh"

        created = run_cli(fresh, "init")
        used = run_cli(fresh, "create", "first ticket")

        self.assertEqual(created.returncode, 0, created.stderr)
        self.assertEqual(used.returncode, 0, used.stderr)
        self.assertEqual(len(next_task.load_all_tickets(fresh)), 1)

    def test_init_on_an_existing_store_leaves_its_tickets_alone(self):
        store = self.parent / "existing"
        run_cli(store, "init")
        run_cli(store, "create", "keep me")

        again = run_cli(store, "init")

        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(next_task.load_all_tickets(store)), 1)


class LoadAllTicketsTest(StoreTestCase):

    def test_leftover_temp_file_from_a_crashed_write_is_ignored(self):
        """A half-written temp file must not brick every command in the store."""
        self.make_ticket("TEST-001")
        (self.tickets_dir / ".tmp_crashed.json").write_text("", encoding="utf-8")

        loaded = next_task.load_all_tickets(self.store)

        self.assertEqual(list(loaded), ["TEST-001"])

    def test_a_json_file_that_is_not_a_ticket_is_ignored(self):
        self.make_ticket("TEST-001")
        (self.tickets_dir / "notes.json").write_text('{"hello": "world"}', encoding="utf-8")

        loaded = next_task.load_all_tickets(self.store)

        self.assertEqual(list(loaded), ["TEST-001"])

    def test_a_transient_read_failure_is_retried(self):
        """Windows refuses an open that lands mid-replace; it succeeds a moment later."""
        self.make_ticket("TEST-001")
        real_open = open
        attempts = []

        def flaky_open(*args, **kwargs):
            attempts.append(args[0])
            if len(attempts) == 1:
                raise PermissionError(13, "Permission denied")
            return real_open(*args, **kwargs)

        with mock.patch("builtins.open", side_effect=flaky_open):
            loaded = next_task.load_all_tickets(self.store)

        self.assertEqual(list(loaded), ["TEST-001"])
        self.assertGreater(len(attempts), 1, "the failed read was never retried")

    def test_a_read_that_never_succeeds_still_raises(self):
        """read_ticket's own contract: retry, then give up honestly. Callers
        decide what to do about it - see MalformedFileTest."""
        self.make_ticket("TEST-001")
        path = self.tickets_dir / "TEST-001.json"

        with mock.patch("builtins.open", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(PermissionError):
                next_task.read_ticket(path)

    def test_real_tickets_are_still_loaded(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002")

        loaded = next_task.load_all_tickets(self.store)

        self.assertEqual(sorted(loaded), ["TEST-001", "TEST-002"])


class AtomicWriteTest(StoreTestCase):

    def test_temp_file_is_never_created_inside_the_tickets_directory(self):
        """Other processes glob that directory; nothing transient may live there."""
        captured = {}
        real_replace = os.replace

        def spy_replace(src, dst):
            captured["tmp"] = Path(src)
            return real_replace(src, dst)

        with mock.patch("next_task.os.replace", side_effect=spy_replace):
            self.make_ticket("TEST-001")

        self.assertNotEqual(
            captured["tmp"].parent.resolve(), self.tickets_dir.resolve(),
            f"temp file {captured['tmp']} was created inside the globbed tickets directory",
        )

    def test_a_failed_write_leaves_nothing_behind(self):
        self.make_ticket("TEST-001")
        before = sorted(p.name for p in self.tickets_dir.iterdir())

        with mock.patch("next_task.json.dump", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.make_ticket("TEST-002")

        after = sorted(p.name for p in self.tickets_dir.iterdir())
        self.assertEqual(before, after)

    def test_data_is_flushed_to_disk_before_the_rename(self):
        """os.replace is only atomic-and-durable if the bytes landed first."""
        calls = []
        real_replace = os.replace

        def spy_replace(src, dst):
            calls.append("replace")
            return real_replace(src, dst)

        with mock.patch("next_task.os.fsync", side_effect=lambda fd: calls.append("fsync")):
            with mock.patch("next_task.os.replace", side_effect=spy_replace):
                self.make_ticket("TEST-001")

        self.assertEqual(calls, ["fsync", "replace"])


class ConcurrentCommandTest(StoreTestCase):

    def concurrent_creates(self, n=6):
        """Two Claude sessions in different projects sharing one store."""
        self.run_cli("create", "seed")
        results = []
        lock = threading.Lock()

        def worker(i):
            proc = self.run_cli("create", f"concurrent {i}")
            with lock:
                results.append(proc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_concurrent_creates_never_trip_over_each_others_temp_files(self):
        """PERS-022: no command may fail while reading the store."""
        offenders = [
            p.stderr for p in self.concurrent_creates()
            if p.returncode != 0 and "load_all_tickets" in p.stderr
        ]
        self.assertEqual(offenders, [], "a command crashed reading the store:\n" + "\n".join(offenders))

    def test_concurrent_creates_all_succeed(self):
        results = self.concurrent_creates()

        failures = [f"exit {p.returncode}: {p.stderr.strip()}" for p in results if p.returncode != 0]
        self.assertEqual(failures, [], "concurrent create crashed:\n" + "\n".join(failures))
        self.assertEqual(len(next_task.load_all_tickets(self.store)), len(results) + 1)


class NonAsciiTest(StoreTestCase):
    """This tool exists because a previous one hit a UTF-8 decode failure on
    Windows and hard-blocked ticket creation. Nothing here may repeat that."""

    AWKWARD = "Résumé — naïve £40 café, 日本語, ✅"

    def test_a_ticket_with_accents_and_symbols_survives_a_round_trip(self):
        proc = self.run_cli("create", self.AWKWARD, "--desc", self.AWKWARD)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        tid = proc.stdout.split()[1].rstrip(":")

        loaded = next_task.load_all_tickets(self.store)[tid]

        self.assertEqual(loaded["title"], self.AWKWARD)
        self.assertEqual(loaded["description"], self.AWKWARD)

    def test_tags_and_source_take_non_ascii_too(self):
        proc = self.run_cli("create", "x", "--tags", "café,naïve", "--source", "Résumé")
        tid = proc.stdout.split()[1].rstrip(":")

        loaded = next_task.load_all_tickets(self.store)[tid]

        self.assertEqual(loaded["tags"], ["café", "naïve"])
        self.assertEqual(loaded["source"], "Résumé")

    def test_it_is_written_as_real_utf8_not_escapes(self):
        """ensure_ascii=False keeps the file readable in an editor."""
        proc = self.run_cli("create", self.AWKWARD)
        tid = proc.stdout.split()[1].rstrip(":")

        raw = (self.tickets_dir / f"{tid}.json").read_text(encoding="utf-8")

        self.assertIn("Résumé", raw)
        self.assertNotIn("\\u00e9", raw)

    def test_editing_keeps_it_intact(self):
        self.make_ticket("TEST-001")

        self.run_cli("update", "TEST-001", "--title", self.AWKWARD)

        self.assertEqual(next_task.load_all_tickets(self.store)["TEST-001"]["title"],
                         self.AWKWARD)

    def test_listing_one_does_not_blow_up(self):
        self.make_ticket("TEST-001", title=self.AWKWARD)

        for command in ("list", "ready", "verify"):
            with self.subTest(command=command):
                self.assertEqual(self.run_cli(command).returncode, 0)


class RemoveDependsTest(StoreTestCase):

    def test_a_dependency_can_be_taken_off_again(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])

        proc = self.run_cli("update", "TEST-002", "--remove-depends", "TEST-001")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(next_task.load_all_tickets(self.store)["TEST-002"]["depends_on"], [])

    def test_removing_the_last_one_unblocks_the_ticket(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])

        proc = self.run_cli("update", "TEST-002", "--remove-depends", "TEST-001")

        self.assertIn("no longer blocked", proc.stdout)
        self.assertIn("TEST-002", self.run_cli("ready").stdout)

    def test_removing_one_that_was_never_there_is_harmless(self):
        self.make_ticket("TEST-001", depends_on=["TEST-002"])

        proc = self.run_cli("update", "TEST-001", "--remove-depends", "TEST-999")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(next_task.load_all_tickets(self.store)["TEST-001"]["depends_on"],
                         ["TEST-002"])


class ShowTest(StoreTestCase):

    def test_it_names_what_this_is_waiting_on_and_what_waits_on_it(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])
        self.make_ticket("TEST-003", depends_on=["TEST-002"])

        stdout = self.run_cli("show", "TEST-002").stdout

        self.assertIn("Blocked on: TEST-001", stdout)
        self.assertIn("Depended on by: TEST-003", stdout)

    def test_a_missing_ticket_is_an_error_not_a_traceback(self):
        proc = self.run_cli("show", "TEST-404")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("No such ticket", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def test_editing_a_missing_ticket_is_an_error_too(self):
        for command in (("update", "TEST-404", "--title", "x"), ("delete", "TEST-404")):
            with self.subTest(command=command[0]):
                proc = self.run_cli(*command)
                self.assertNotEqual(proc.returncode, 0)
                self.assertNotIn("Traceback", proc.stderr)


class EmptyStoreTest(StoreTestCase):

    def test_each_command_says_something_sensible(self):
        expected = {"list": "No tickets.", "ready": "Nothing ready.",
                    "blocked": "Nothing blocked.", "verify": "OK - 0 tickets"}

        for command, message in expected.items():
            with self.subTest(command=command):
                proc = self.run_cli(command)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(message, proc.stdout)


class DisplaySourceTest(unittest.TestCase):

    def test_a_long_project_name_is_shortened_to_keep_columns_straight(self):
        shown = next_task.display_source({"source": "a-really-long-folder-name"})

        self.assertLessEqual(len(shown), next_task.SOURCE_WIDTH)
        self.assertTrue(shown.endswith("~"))

    def test_a_short_one_is_left_alone(self):
        self.assertEqual(next_task.display_source({"source": "gen-art"}), "gen-art")

    def test_whitespace_only_counts_as_absent(self):
        self.assertEqual(next_task.display_source({"source": "   "}), "-")


class LifecycleTest(StoreTestCase):
    """The sequence CLAUDE.md names as the gate on any dependency change:
    create, block, complete, unblock, un-complete, re-block."""

    def blocked_now(self, tid):
        loaded = next_task.load_all_tickets(self.store)
        return next_task.is_blocked(loaded[tid], loaded)

    def test_the_whole_sequence(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])
        self.assertTrue(self.blocked_now("TEST-002"), "should start blocked")
        self.assertNotIn("TEST-002", self.run_cli("ready").stdout)

        done = self.run_cli("update", "TEST-001", "--status", "done")
        self.assertIn("Unblocked: TEST-002", done.stdout)
        self.assertFalse(self.blocked_now("TEST-002"), "should be free once the prerequisite is done")
        self.assertIn("TEST-002", self.run_cli("ready").stdout)

        reopened = self.run_cli("update", "TEST-001", "--status", "open")
        self.assertIn("Re-blocked: TEST-002", reopened.stdout)
        self.assertTrue(self.blocked_now("TEST-002"), "should block again")
        self.assertIn("TEST-002", self.run_cli("blocked").stdout)

        self.assertEqual(self.run_cli("verify").returncode, 0)

    def test_cancelling_a_prerequisite_also_releases_its_dependents(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])

        self.run_cli("update", "TEST-001", "--status", "cancelled")

        self.assertFalse(self.blocked_now("TEST-002"))


class DeleteGuardTest(StoreTestCase):

    def test_deleting_something_depended_on_is_refused(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])

        proc = self.run_cli("delete", "TEST-001")

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("TEST-002", proc.stderr)
        self.assertIn("TEST-001", next_task.load_all_tickets(self.store))

    def test_force_overrides_it_and_verify_then_objects(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])

        self.run_cli("delete", "TEST-001", "--force")

        self.assertNotIn("TEST-001", next_task.load_all_tickets(self.store))
        self.assertEqual(self.run_cli("verify").returncode, 1)

    def test_deleting_something_nothing_depends_on_just_works(self):
        self.make_ticket("TEST-001")

        proc = self.run_cli("delete", "TEST-001")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(next_task.load_all_tickets(self.store), {})


class ReadyOrderingTest(StoreTestCase):

    def test_ready_is_ordered_by_priority_then_id(self):
        self.make_ticket("TEST-001", priority="low")
        self.make_ticket("TEST-002", priority="critical")
        self.make_ticket("TEST-003", priority="medium")
        self.make_ticket("TEST-004", priority="critical")

        listed = [line.split()[0] for line in
                  self.run_cli("ready").stdout.strip().splitlines()]

        self.assertEqual(listed, ["TEST-002", "TEST-004", "TEST-003", "TEST-001"])

    def test_finished_and_blocked_tickets_are_left_out(self):
        self.make_ticket("TEST-001", status="done")
        self.make_ticket("TEST-002")
        self.make_ticket("TEST-003", depends_on=["TEST-002"])

        stdout = self.run_cli("ready").stdout

        self.assertIn("TEST-002", stdout)
        self.assertNotIn("TEST-001", stdout)
        self.assertNotIn("TEST-003", stdout)


class WouldUnlockTest(StoreTestCase):

    def test_it_names_what_finishing_this_would_release(self):
        import dashboard
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])
        loaded = next_task.load_all_tickets(self.store)

        self.assertEqual(dashboard.would_unlock("TEST-001", loaded), ["TEST-002"])

    def test_it_stays_quiet_when_another_prerequisite_remains(self):
        import dashboard
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002")
        self.make_ticket("TEST-003", depends_on=["TEST-001", "TEST-002"])
        loaded = next_task.load_all_tickets(self.store)

        self.assertEqual(dashboard.would_unlock("TEST-001", loaded), [])

    def test_it_ignores_dependents_that_are_already_finished(self):
        import dashboard
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"], status="done")
        loaded = next_task.load_all_tickets(self.store)

        self.assertEqual(dashboard.would_unlock("TEST-001", loaded), [])


class ConcurrentUpdateTest(StoreTestCase):
    """update reads the whole store, changes it and writes back. Two sessions
    doing that at once used to lose one of the edits silently."""

    def test_simultaneous_edits_to_one_ticket_all_survive(self):
        self.make_ticket("TEST-001")
        for n in range(1, 7):
            self.make_ticket(f"TEST-10{n}")
        results = []
        lock = threading.Lock()

        def worker(n):
            proc = self.run_cli("update", "TEST-001", "--add-depends", f"TEST-10{n}")
            with lock:
                results.append(proc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(1, 7)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        failures = [p.stderr for p in results if p.returncode != 0]
        self.assertEqual(failures, [], "an update failed:\n" + "\n".join(failures))
        deps = next_task.load_all_tickets(self.store)["TEST-001"]["depends_on"]
        self.assertEqual(sorted(deps), [f"TEST-10{n}" for n in range(1, 7)])

    def test_the_lock_is_released_after_a_normal_update(self):
        self.make_ticket("TEST-001")

        self.run_cli("update", "TEST-001", "--title", "first")
        second = self.run_cli("update", "TEST-001", "--title", "second")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(next_task.load_all_tickets(self.store)["TEST-001"]["title"], "second")

    def test_the_lock_is_released_even_when_the_update_blows_up(self):
        self.make_ticket("TEST-001")

        with mock.patch.object(next_task, "save_ticket", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                with next_task.store_lock(self.store):
                    next_task.save_ticket(self.store, {})

        after = self.run_cli("update", "TEST-001", "--title", "still works")
        self.assertEqual(after.returncode, 0, after.stderr)

    def test_a_lock_left_by_a_killed_process_does_not_block_forever(self):
        self.make_ticket("TEST-001")
        stale = next_task.temp_dir(self.store) / next_task.LOCK_NAME
        stale.write_text("", encoding="utf-8")
        old = time.time() - (next_task.LOCK_STALE_AFTER + 10)
        os.utime(stale, (old, old))

        proc = self.run_cli("update", "TEST-001", "--title", "recovered")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(next_task.load_all_tickets(self.store)["TEST-001"]["title"], "recovered")

    def test_a_lock_held_right_now_makes_the_command_give_up_clearly(self):
        self.make_ticket("TEST-001")
        held = next_task.temp_dir(self.store) / next_task.LOCK_NAME
        held.write_text("", encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "next_task.py"), "--store", str(self.store),
             "update", "TEST-001", "--title", "should not land"],
            capture_output=True, text=True, env={**os.environ, "TICKETS_LOCK_TIMEOUT": "0.3"},
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("lock", proc.stderr.lower())
        self.assertNotEqual(next_task.load_all_tickets(self.store)["TEST-001"]["title"],
                            "should not land")


class CycleTest(StoreTestCase):
    """A dependency loop means none of those tickets can ever be ready, and
    nothing used to say so: 'ready' just quietly omitted them."""

    def loop(self, *ids):
        for i, tid in enumerate(ids):
            self.make_ticket(tid, depends_on=[ids[(i + 1) % len(ids)]])

    def test_verify_reports_a_two_ticket_loop(self):
        self.loop("TEST-001", "TEST-002")

        proc = self.run_cli("verify")

        self.assertIn("TEST-001", proc.stdout)
        self.assertIn("TEST-002", proc.stdout)
        self.assertIn("circular", proc.stdout.lower())
        self.assertEqual(proc.returncode, 1)

    def test_verify_reports_a_longer_loop(self):
        self.loop("TEST-001", "TEST-002", "TEST-003")

        proc = self.run_cli("verify")

        self.assertIn("circular", proc.stdout.lower())
        for tid in ("TEST-001", "TEST-002", "TEST-003"):
            self.assertIn(tid, proc.stdout)

    def test_verify_says_nothing_about_cycles_in_a_healthy_chain(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])
        self.make_ticket("TEST-003", depends_on=["TEST-002"])

        proc = self.run_cli("verify")

        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertNotIn("circular", proc.stdout.lower())

    def next_id_will_be(self):
        """The ID the next create will hand out, so a test can point at it."""
        return f"{next_task.id_prefix(self.store)}-001"

    def test_create_refuses_a_dependency_that_would_close_a_loop(self):
        """Reachable because a ticket can point at an ID issued later."""
        self.make_ticket("TEST-001", depends_on=[self.next_id_will_be()])

        proc = self.run_cli("create", "the other half", "--depends-on", "TEST-001")

        self.assertIn("circular", proc.stderr.lower())
        created = next_task.load_all_tickets(self.store)[self.next_id_will_be()]
        self.assertEqual(created["depends_on"], [])

    def test_create_refuses_a_ticket_depending_on_itself(self):
        mine = self.next_id_will_be()

        proc = self.run_cli("create", "self referential", "--depends-on", mine)

        self.assertIn("circular", proc.stderr.lower())
        self.assertEqual(next_task.load_all_tickets(self.store)[mine]["depends_on"], [])

    def test_update_still_refuses_to_close_a_loop(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])

        self.run_cli("update", "TEST-001", "--add-depends", "TEST-002")

        self.assertEqual(next_task.load_all_tickets(self.store)["TEST-001"]["depends_on"], [])


class SmallCorrectnessTest(StoreTestCase):
    """The leftovers from the review: each small, each user-visible."""

    def test_listing_finished_tickets_does_not_need_all_as_well(self):
        self.make_ticket("TEST-001", status="done")
        self.make_ticket("TEST-002")

        proc = self.run_cli("list", "--status", "done")

        self.assertIn("TEST-001", proc.stdout)
        self.assertNotIn("TEST-002", proc.stdout)

    def test_asking_for_open_still_hides_finished_ones(self):
        self.make_ticket("TEST-001", status="done")
        self.make_ticket("TEST-002")

        proc = self.run_cli("list", "--status", "open")

        self.assertIn("TEST-002", proc.stdout)
        self.assertNotIn("TEST-001", proc.stdout)

    def test_verify_fails_when_it_finds_problems(self):
        """So it can be chained or used in a hook."""
        self.make_ticket("TEST-001", depends_on=["TEST-999"])

        proc = self.run_cli("verify")

        self.assertNotEqual(proc.returncode, 0)

    def test_verify_succeeds_on_a_healthy_store(self):
        self.make_ticket("TEST-001")

        proc = self.run_cli("verify")

        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_update_survives_a_ticket_with_no_depends_on_field(self):
        ticket = {
            "id": "TEST-001", "title": "hand edited", "description": "",
            "status": "open", "priority": "medium", "tags": [], "source": "",
            "created_at": next_task.now_iso(), "updated_at": next_task.now_iso(),
        }
        next_task.save_ticket(self.store, ticket)
        self.make_ticket("TEST-002")

        proc = self.run_cli("update", "TEST-001", "--add-depends", "TEST-002")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(next_task.load_all_tickets(self.store)["TEST-001"]["depends_on"],
                         ["TEST-002"])

    def test_ids_are_listed_in_numeric_order(self):
        """Plain text order would put TEST-1000 before TEST-999."""
        self.make_ticket("TEST-999")
        self.make_ticket("TEST-1000")

        stdout = self.run_cli("list").stdout

        self.assertLess(stdout.index("TEST-999"), stdout.index("TEST-1000"))

    def test_finishing_a_ticket_does_not_announce_cancelled_dependents(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"], status="cancelled")

        proc = self.run_cli("update", "TEST-001", "--status", "done")

        self.assertNotIn("Unblocked", proc.stdout)

    def test_finishing_a_ticket_still_announces_live_dependents(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])

        proc = self.run_cli("update", "TEST-001", "--status", "done")

        self.assertIn("Unblocked: TEST-002", proc.stdout)


class DashboardLauncherTest(unittest.TestCase):
    """PERS-035: started from a SessionStart hook, so it has to be silent,
    idempotent, and incapable of holding up the session."""

    def setUp(self):
        import dashboard_launch
        self.launch = dashboard_launch
        self.tmp = Path(tempfile.mkdtemp(prefix="tickettest_"))
        self.project = self.tmp / "a-project"
        self.project.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mark(self):
        (self.project / ".ticket-scope").write_text("personal", encoding="utf-8")

    def test_a_folder_with_no_marker_is_not_a_ticket_project(self):
        self.assertFalse(self.launch.in_a_ticket_project(self.project))

    def test_a_marker_in_a_parent_folder_counts(self):
        self.mark()
        deep = self.project / "src" / "deep"
        deep.mkdir(parents=True)

        self.assertTrue(self.launch.in_a_ticket_project(deep))

    def test_a_free_port_reads_as_not_listening(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            free = probe.getsockname()[1]

        self.assertFalse(self.launch.already_listening(free))

    def test_a_bound_port_reads_as_listening(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]

            self.assertTrue(self.launch.already_listening(port))

    def test_it_does_nothing_outside_a_ticket_project(self):
        with mock.patch.object(self.launch, "spawn") as spawn, \
             mock.patch.object(self.launch, "already_listening", return_value=False), \
             mock.patch("webbrowser.open") as browser:
            result = self.launch.main(cwd=self.project)

        self.assertEqual(result, 0)
        spawn.assert_not_called()
        browser.assert_not_called()

    def test_it_starts_nothing_and_opens_nothing_if_already_running(self):
        """A second session must not spawn a rival server or a new tab."""
        self.mark()
        with mock.patch.object(self.launch, "spawn") as spawn, \
             mock.patch.object(self.launch, "already_listening", return_value=True), \
             mock.patch("webbrowser.open") as browser:
            result = self.launch.main(cwd=self.project)

        self.assertEqual(result, 0)
        spawn.assert_not_called()
        browser.assert_not_called()

    def test_it_starts_the_server_and_opens_one_tab_when_down(self):
        self.mark()
        with mock.patch.object(self.launch, "spawn") as spawn, \
             mock.patch.object(self.launch, "already_listening", return_value=False), \
             mock.patch.object(self.launch, "wait_until_listening", return_value=True), \
             mock.patch("webbrowser.open") as browser:
            result = self.launch.main(cwd=self.project)

        self.assertEqual(result, 0)
        spawn.assert_called_once()
        browser.assert_called_once()
        self.assertIn("127.0.0.1", browser.call_args[0][0])

    def test_no_tab_is_opened_if_the_server_never_came_up(self):
        self.mark()
        with mock.patch.object(self.launch, "spawn"), \
             mock.patch.object(self.launch, "already_listening", return_value=False), \
             mock.patch.object(self.launch, "wait_until_listening", return_value=False), \
             mock.patch("webbrowser.open") as browser:
            self.launch.main(cwd=self.project)

        browser.assert_not_called()

    def test_it_exits_quietly_even_when_something_goes_wrong(self):
        """A hook that fails loudly at every session start gets old fast."""
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "dashboard_launch.py"), "--port", "not-a-number"],
            capture_output=True, text=True, cwd=self.project,
        )

        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_running_it_outside_a_project_is_a_silent_no_op(self):
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "dashboard_launch.py")],
            capture_output=True, text=True, cwd=self.project,
        )

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


class DashboardPageTest(unittest.TestCase):
    """PERS-034: sortable columns, a source filter, and in-progress visible."""

    def setUp(self):
        import dashboard
        self.dashboard = dashboard
        self.root = Path(tempfile.mkdtemp(prefix="tickettest_"))
        self.store = self.root / "demo"
        (self.store / "tickets").mkdir(parents=True)
        self.add("DEMO-001", priority="high", source="gen-art")
        self.add("DEMO-002", priority="low", source="linkedin", status="in_progress")
        self.add("DEMO-003", priority="critical", source="gen-art", depends_on=["DEMO-001"])
        self.add("DEMO-004", priority="medium", status="done")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def add(self, tid, **overrides):
        ticket = {
            "id": tid, "title": f"title of {tid}", "description": "", "status": "open",
            "priority": "medium", "tags": [], "source": "", "depends_on": [],
            "created_at": next_task.now_iso(), "updated_at": next_task.now_iso(),
        }
        ticket.update(overrides)
        next_task.save_ticket(self.store, ticket)

    def page(self):
        old = self.dashboard.STORES_ROOT
        self.dashboard.STORES_ROOT = self.root
        try:
            with self.dashboard.app.test_client() as client:
                response = client.get("/")
                self.assertEqual(response.status_code, 200)
                return response.get_data(as_text=True)
        finally:
            self.dashboard.STORES_ROOT = old

    def test_the_filter_lists_each_source_once(self):
        html = self.page()

        self.assertEqual(html.count('value="gen-art"'), 1)
        self.assertEqual(html.count('value="linkedin"'), 1)

    def test_every_row_carries_its_source_for_filtering(self):
        html = self.page()

        self.assertIn('data-source="gen-art"', html)
        self.assertIn('data-source="linkedin"', html)

    def test_a_ticket_in_progress_is_distinguishable(self):
        view = self.dashboard.build_store_view(self.store)

        in_progress = [r for r in view["ready"] if r["status"] == "in_progress"]
        self.assertEqual([r["id"] for r in in_progress], ["DEMO-002"])
        self.assertIn("in progress", self.page().lower())

    def test_priority_sorts_by_rank_not_alphabetically(self):
        """'critical' before 'high' before 'low' - never c/h/l as text."""
        view = self.dashboard.build_store_view(self.store)

        ranks = {r["id"]: r["_rank"] for r in view["ready"] + view["blocked"]}
        self.assertGreater(ranks["DEMO-003"], ranks["DEMO-001"])
        self.assertGreater(ranks["DEMO-001"], ranks["DEMO-002"])

    def test_ids_sort_numerically_not_as_text(self):
        self.add("DEMO-010")
        view = self.dashboard.build_store_view(self.store)

        nums = {r["id"]: r["_num"] for r in view["ready"]}
        self.assertGreater(nums["DEMO-010"], nums["DEMO-002"])

    def test_the_sortable_columns_are_marked_up(self):
        html = self.page()

        for column in ("id", "created", "source", "priority"):
            self.assertIn(f'data-sort-key="{column}"', html)

    def test_the_page_still_has_no_way_to_write_anything(self):
        """The read-only rule survives adding interactivity."""
        html = self.page()

        self.assertNotIn("<form", html.lower())
        methods = set()
        for rule in self.dashboard.app.url_map.iter_rules():
            methods |= rule.methods - {"HEAD", "OPTIONS"}
        self.assertEqual(methods, {"GET"})

    def test_a_ticket_with_no_source_still_appears(self):
        html = self.page()

        self.assertIn("DEMO-004", html)


class ScopeMarkerTest(unittest.TestCase):
    """PERS-033: the store is currently a path retyped on every call, and the
    personal/work boundary depends on getting it right each time.

    Resolution is tested against an explicit root, never the real one - the
    marker maps a name onto a folder, and 'personal' would be real data."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tickettest_"))
        self.root = self.tmp / "stores"
        (self.root / "personal").mkdir(parents=True)
        self.project = self.tmp / "some-project"
        self.project.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def marker(self, text, at=None):
        (at or self.project).joinpath(".ticket-scope").write_text(text, encoding="utf-8")

    def test_a_marker_in_this_folder_names_the_store(self):
        self.marker("personal")

        self.assertEqual(next_task.resolve_store(self.project, self.root),
                         self.root / "personal")

    def test_a_marker_in_a_parent_folder_is_found(self):
        self.marker("personal")
        deep = self.project / "src" / "components"
        deep.mkdir(parents=True)

        self.assertEqual(next_task.resolve_store(deep, self.root),
                         self.root / "personal")

    def test_trailing_whitespace_and_blank_lines_are_tolerated(self):
        self.marker("\n  personal  \n\n")

        self.assertEqual(next_task.resolve_store(self.project, self.root),
                         self.root / "personal")

    def test_no_marker_anywhere_resolves_to_nothing(self):
        self.assertIsNone(next_task.resolve_store(self.project, self.root))

    def test_a_marker_cannot_point_outside_the_store_root(self):
        for attack in ("../../elsewhere", "..", "a/b", r"a\b", "/etc", ""):
            with self.subTest(attack=attack):
                self.marker(attack)
                with self.assertRaises(ValueError):
                    next_task.resolve_store(self.project, self.root)

    def test_a_second_line_in_the_marker_names_the_source(self):
        """Where scopes split across subfolders the marker sits in one of them,
        and that folder name ('general') is a poor answer for 'which project'."""
        self.marker("personal\nAI Governance")

        self.assertEqual(next_task.default_source(self.project), "AI Governance")

    def test_the_named_source_survives_running_from_a_subfolder(self):
        self.marker("personal\nAI Governance")
        deep = self.project / "general" / "notes"
        deep.mkdir(parents=True)

        self.assertEqual(next_task.default_source(deep), "AI Governance")

    def test_a_named_source_does_not_disturb_the_scope(self):
        self.marker("personal\nAI Governance")

        self.assertEqual(next_task.resolve_store(self.project, self.root),
                         self.root / "personal")

    def test_blank_lines_around_the_two_entries_are_tolerated(self):
        self.marker("\n  personal  \n\n   AI Governance   \n\n")

        self.assertEqual(next_task.resolve_store(self.project, self.root), self.root / "personal")
        self.assertEqual(next_task.default_source(self.project), "AI Governance")

    def test_a_marker_with_only_a_scope_still_uses_its_folder(self):
        self.marker("personal")

        self.assertEqual(next_task.default_source(self.project), "some-project")

    def test_a_named_source_may_contain_spaces_and_punctuation(self):
        """It's a label, not a path - it only has to read well in a column."""
        self.marker("work\nAcme / support")

        self.assertEqual(next_task.default_source(self.project), "Acme / support")

    def test_source_defaults_to_the_folder_holding_the_marker(self):
        """Run from a subfolder, the project name is still what's recorded."""
        self.marker("personal")
        deep = self.project / "src"
        deep.mkdir()

        self.assertEqual(next_task.default_source(deep), "some-project")

    def test_source_falls_back_to_the_current_folder(self):
        self.assertEqual(next_task.default_source(self.project), "some-project")


class ScopeMarkerCliTest(StoreTestCase):

    def test_store_is_still_required_when_no_marker_can_be_found(self):
        elsewhere = Path(tempfile.mkdtemp(prefix="tickettest_"))
        self.addCleanup(shutil.rmtree, elsewhere, ignore_errors=True)

        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "next_task.py"), "list"],
            capture_output=True, text=True, cwd=elsewhere,
        )

        self.assertNotEqual(proc.returncode, 0)
        self.assertIn(".ticket-scope", proc.stderr)

    def test_an_explicit_store_still_wins(self):
        self.make_ticket("TEST-001")

        proc = self.run_cli("list")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("TEST-001", proc.stdout)


class UpdateOptionsTest(StoreTestCase):
    """PERS-030: priority drives the ready ordering, so it can't be frozen at
    whatever was guessed when the ticket was created."""

    def loaded(self, tid="TEST-001"):
        return next_task.load_all_tickets(self.store)[tid]

    def test_priority_can_be_changed(self):
        self.make_ticket("TEST-001", priority="low")

        proc = self.run_cli("update", "TEST-001", "--priority", "high")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.loaded()["priority"], "high")

    def test_an_invalid_priority_is_refused(self):
        self.make_ticket("TEST-001", priority="low")

        proc = self.run_cli("update", "TEST-001", "--priority", "urgent")

        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.loaded()["priority"], "low")

    def test_tags_can_be_replaced(self):
        self.make_ticket("TEST-001", tags=["old"])

        self.run_cli("update", "TEST-001", "--tags", "alpha,beta")

        self.assertEqual(self.loaded()["tags"], ["alpha", "beta"])

    def test_tags_can_be_cleared(self):
        self.make_ticket("TEST-001", tags=["old"])

        self.run_cli("update", "TEST-001", "--tags", "")

        self.assertEqual(self.loaded()["tags"], [])

    def test_empty_segments_are_not_stored(self):
        self.make_ticket("TEST-001")

        self.run_cli("update", "TEST-001", "--tags", "alpha,,beta,")

        self.assertEqual(self.loaded()["tags"], ["alpha", "beta"])

    def test_leaving_options_out_changes_nothing(self):
        self.make_ticket("TEST-001", priority="low", tags=["keep"])

        self.run_cli("update", "TEST-001", "--title", "new title")

        after = self.loaded()
        self.assertEqual(after["priority"], "low")
        self.assertEqual(after["tags"], ["keep"])


class SourceFieldTest(StoreTestCase):
    """PERS-031: tickets arrive from many project folders; record which."""

    def loaded(self, tid):
        return next_task.load_all_tickets(self.store)[tid]

    def created_id(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.split()[1].rstrip(":")

    def test_a_new_ticket_records_the_folder_it_was_created_in(self):
        project = Path(tempfile.mkdtemp(prefix="tickettest_")) / "some-project"
        project.mkdir()
        self.addCleanup(shutil.rmtree, project.parent, ignore_errors=True)

        tid = self.created_id(run_cli(self.store, "create", "from a project", cwd=project))

        self.assertEqual(self.loaded(tid)["source"], "some-project")

    def test_an_explicit_source_wins(self):
        tid = self.created_id(self.run_cli("create", "x", "--source", "elsewhere"))

        self.assertEqual(self.loaded(tid)["source"], "elsewhere")

    def test_source_can_be_corrected_afterwards(self):
        self.make_ticket("TEST-001", source="wrong")

        self.run_cli("update", "TEST-001", "--source", "right")

        self.assertEqual(self.loaded("TEST-001")["source"], "right")

    def test_an_older_ticket_with_no_source_still_works(self):
        """Nothing is backfilled, so most existing tickets have no source."""
        ticket = {
            "id": "TEST-001", "title": "old", "description": "", "status": "open",
            "priority": "medium", "tags": [], "depends_on": [],
            "created_at": next_task.now_iso(), "updated_at": next_task.now_iso(),
        }
        next_task.save_ticket(self.store, ticket)

        proc = self.run_cli("list")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("TEST-001", proc.stdout)

    def test_the_placeholder_is_plain_ascii(self):
        """The console here is not reliably UTF-8 - that is what broke the
        previous tool. A dash that cannot be encoded is not an improvement."""
        blank = next_task.display_source({})

        blank.encode("ascii")
        self.assertTrue(blank)

    def test_the_dashboard_carries_a_source_for_each_row(self):
        import dashboard
        self.make_ticket("TEST-001", source="proj")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])
        self.make_ticket("TEST-003", status="done")

        view = dashboard.build_store_view(self.store)

        for section in ("ready", "blocked", "other"):
            for row in view[section]:
                self.assertIn("_source", row, section)


class LocalTimeTest(unittest.TestCase):
    """Timestamps are stored UTC. Displaying them raw is wrong by the local
    offset, and near midnight that's wrong by a whole day."""

    def test_the_same_instant_in_two_offsets_gives_the_same_local_date(self):
        """Fails if the date is sliced off the string instead of converted."""
        utc = "2026-08-05T23:30:00+00:00"
        elsewhere = "2026-08-06T01:30:00+02:00"

        self.assertEqual(next_task.local_time(utc), next_task.local_time(elsewhere))

    def test_a_timestamp_with_no_offset_is_read_as_utc(self):
        self.assertEqual(
            next_task.local_time("2026-08-05T23:30:00"),
            next_task.local_time("2026-08-05T23:30:00+00:00"),
        )

    def test_it_returns_a_plain_iso_date_by_default(self):
        self.assertRegex(next_task.local_time("2026-08-05T12:00:00+00:00"), r"^\d{4}-\d{2}-\d{2}$")

    def test_a_missing_or_broken_timestamp_does_not_crash(self):
        for bad in ("", "not a date", None, "2026-13-45T99:99:99"):
            with self.subTest(bad=bad):
                self.assertIsInstance(next_task.local_time(bad), str)


class DateDisplayTest(StoreTestCase):

    def test_list_shows_a_date(self):
        self.make_ticket("TEST-001")

        proc = self.run_cli("list")

        self.assertRegex(proc.stdout, r"\d{4}-\d{2}-\d{2}")

    def test_ready_shows_a_date(self):
        self.make_ticket("TEST-001")

        proc = self.run_cli("ready")

        self.assertRegex(proc.stdout, r"\d{4}-\d{2}-\d{2}")

    def test_blocked_shows_a_date(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])

        proc = self.run_cli("blocked")

        self.assertRegex(proc.stdout, r"\d{4}-\d{2}-\d{2}")

    def test_show_spells_out_the_local_time(self):
        self.make_ticket("TEST-001")

        proc = self.run_cli("show", "TEST-001")

        self.assertIn("local time", proc.stdout.lower())
        self.assertRegex(proc.stdout, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")

    def test_a_ticket_with_a_broken_timestamp_still_lists(self):
        self.make_ticket("TEST-001", created_at="whenever")

        proc = self.run_cli("list")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("TEST-001", proc.stdout)

    def test_the_dashboard_carries_a_date_for_each_row(self):
        import dashboard
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])
        self.make_ticket("TEST-003", status="done")

        view = dashboard.build_store_view(self.store)

        for section in ("ready", "blocked", "other"):
            for row in view[section]:
                self.assertRegex(row["_created"], r"^\d{4}-\d{2}-\d{2}$", section)


class MalformedFileTest(StoreTestCase):
    """One bad file must not take down every command in the store - least of
    all 'verify', whose whole job is finding bad files."""

    def corrupt(self, name="TEST-002.json", content="{ this is not json"):
        self.make_ticket("TEST-001")
        (self.tickets_dir / name).write_text(content, encoding="utf-8")

    def test_other_tickets_still_list(self):
        self.corrupt()

        proc = self.run_cli("list")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("TEST-001", proc.stdout)

    def test_the_skipped_file_is_flagged_on_stderr(self):
        self.corrupt()

        proc = self.run_cli("list")

        self.assertIn("verify", proc.stderr.lower())

    def test_verify_names_the_bad_file_instead_of_crashing(self):
        self.corrupt()

        proc = self.run_cli("verify")

        self.assertIn("TEST-002.json", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertEqual(proc.returncode, 1, "a reported problem should fail the command")

    def test_valid_json_that_is_not_a_ticket_is_reported(self):
        self.corrupt(content='["not", "a", "ticket"]')

        proc = self.run_cli("verify")

        self.assertIn("TEST-002.json", proc.stdout)

    def test_a_ticket_with_no_id_is_reported(self):
        self.corrupt(content='{"title": "no id here"}')

        proc = self.run_cli("verify")

        self.assertIn("TEST-002.json", proc.stdout)

    def test_a_file_that_cannot_be_read_at_all_is_reported_not_raised(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002")
        bad = self.tickets_dir / "TEST-002.json"
        real_read = next_task.read_ticket

        def deny(path):
            if Path(path) == bad:
                raise PermissionError(13, "Permission denied")
            return real_read(path)

        with mock.patch("next_task.read_ticket", side_effect=deny):
            loaded, problems = next_task.load_store(self.store)

        self.assertEqual(list(loaded), ["TEST-001"])
        self.assertTrue(any("TEST-002.json" in p for p in problems), problems)

    def test_the_dashboard_survives_and_counts_it(self):
        import dashboard
        self.corrupt()

        view = dashboard.build_store_view(self.store)

        self.assertEqual(view["total"], 1)
        self.assertEqual(len(view["unreadable"]), 1)


class IdIntegrityTest(StoreTestCase):
    """A ticket that vanishes while verify reports a clean store is the worst
    combination available, so both halves are checked."""

    def write_raw(self, filename, ticket_id, title="hand written"):
        (self.tickets_dir / filename).write_text(json.dumps({
            "id": ticket_id, "title": title, "description": "", "status": "open",
            "priority": "medium", "tags": [], "source": "", "depends_on": [],
            "created_at": next_task.now_iso(), "updated_at": next_task.now_iso(),
        }, indent=2), encoding="utf-8")

    def test_a_filename_that_disagrees_with_its_id_is_reported(self):
        self.write_raw("TEST-005.json", "TEST-009")

        proc = self.run_cli("verify")

        self.assertIn("TEST-005.json", proc.stdout)
        self.assertEqual(proc.returncode, 1)

    def test_two_files_claiming_one_id_are_reported(self):
        self.write_raw("TEST-001.json", "TEST-001", title="the real one")
        self.write_raw("TEST-002.json", "TEST-001", title="the impostor")

        proc = self.run_cli("verify")

        self.assertIn("TEST-002.json", proc.stdout)
        self.assertIn("TEST-001", proc.stdout)
        self.assertEqual(proc.returncode, 1)

    def test_the_duplicate_does_not_silently_replace_the_original(self):
        self.write_raw("TEST-001.json", "TEST-001", title="the real one")
        self.write_raw("TEST-002.json", "TEST-001", title="the impostor")

        loaded, problems = next_task.load_store(self.store)

        self.assertEqual(loaded["TEST-001"]["title"], "the real one")
        self.assertTrue(problems)

    def test_ordinary_commands_say_something_is_wrong(self):
        self.write_raw("TEST-001.json", "TEST-001")
        self.write_raw("TEST-002.json", "TEST-001")

        proc = self.run_cli("list")

        self.assertIn("verify", proc.stderr.lower())

    def test_a_healthy_store_raises_none_of_this(self):
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002")

        loaded, problems = next_task.load_store(self.store)

        self.assertEqual(problems, [])
        self.assertEqual(sorted(loaded), ["TEST-001", "TEST-002"])


class IdReuseTest(StoreTestCase):
    """A number, once handed out, is never handed out again."""

    def created_id(self, title):
        proc = self.run_cli("create", title)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.split()[1].rstrip(":")

    def test_deleting_the_highest_ticket_does_not_free_its_number(self):
        self.created_id("first")
        highest = self.created_id("second")
        self.run_cli("delete", highest, "--force")

        replacement = self.created_id("something unrelated")

        self.assertNotEqual(replacement, highest)

    def test_a_new_ticket_does_not_inherit_the_deleted_one_s_dependents(self):
        # The prerequisite has to be the highest number, or nothing is reused.
        dependent = self.created_id("waits for something")
        prerequisite = self.created_id("prerequisite")
        self.run_cli("update", dependent, "--add-depends", prerequisite)
        self.run_cli("delete", prerequisite, "--force")

        self.created_id("unrelated new work")
        loaded = next_task.load_all_tickets(self.store)

        self.assertIn(prerequisite, next_task.missing_deps(loaded[dependent], loaded),
                      "a new ticket silently took over the deleted ID")

    def test_a_store_upgraded_from_before_the_ledger_is_still_protected(self):
        """Existing stores have no record of past IDs when this first runs."""
        self.created_id("first")
        highest = self.created_id("second")
        shutil.rmtree(self.store / ".ids", ignore_errors=True)
        self.run_cli("delete", highest, "--force")

        replacement = self.created_id("something unrelated")

        self.assertNotEqual(replacement, highest)

    def test_numbering_is_still_sequential_in_normal_use(self):
        ids = [self.created_id(f"ticket {i}") for i in range(3)]

        self.assertEqual([i.split("-")[-1] for i in ids], ["001", "002", "003"])


class StoreNamingTest(unittest.TestCase):
    """The ID prefix comes from the folder name, so the loader has to accept
    every prefix the tool can actually generate."""

    def setUp(self):
        self.parent = Path(tempfile.mkdtemp(prefix="tickettest_"))

    def tearDown(self):
        shutil.rmtree(self.parent, ignore_errors=True)

    def test_a_store_whose_name_starts_with_digits_keeps_its_tickets(self):
        store = self.parent / "2026plan"
        run_cli(store, "init")

        run_cli(store, "create", "first")

        self.assertEqual(len(next_task.load_all_tickets(store)), 1)

    def test_a_store_whose_name_has_punctuation_keeps_its_tickets(self):
        store = self.parent / "my.store"
        run_cli(store, "init")

        run_cli(store, "create", "first")

        self.assertEqual(len(next_task.load_all_tickets(store)), 1)

    def test_a_second_ticket_can_depend_on_the_first(self):
        """The symptom: tickets the loader drops look like they never existed,
        so dependencies read as unknown and IDs get handed out twice."""
        store = self.parent / "2026plan"
        run_cli(store, "init")
        first = run_cli(store, "create", "first")
        tid = first.stdout.split()[1].rstrip(":")

        second = run_cli(store, "create", "second", "--depends-on", tid)

        self.assertNotIn("unknown dependency", second.stderr)
        self.assertEqual(len(next_task.load_all_tickets(store)), 2)


class DependencyBlockingTest(StoreTestCase):
    """A dependency that can't be found is unknown, not finished."""

    def orphan(self):
        """TEST-002 depends on TEST-001, which no longer exists."""
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])
        (self.tickets_dir / "TEST-001.json").unlink()
        return next_task.load_all_tickets(self.store)

    def test_a_ticket_whose_dependency_was_deleted_is_blocked(self):
        loaded = self.orphan()

        self.assertTrue(next_task.is_blocked(loaded["TEST-002"], loaded))

    def test_ready_does_not_offer_a_ticket_with_a_missing_dependency(self):
        self.orphan()

        proc = self.run_cli("ready")

        self.assertNotIn("TEST-002", proc.stdout)

    def test_blocked_lists_it_and_says_the_dependency_is_missing(self):
        self.orphan()

        proc = self.run_cli("blocked")

        self.assertIn("TEST-002", proc.stdout)
        self.assertIn("TEST-001", proc.stdout)
        self.assertIn("missing", proc.stdout.lower())

    def test_a_finished_dependency_still_unblocks(self):
        self.make_ticket("TEST-001", status="done")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])
        loaded = next_task.load_all_tickets(self.store)

        self.assertFalse(next_task.is_blocked(loaded["TEST-002"], loaded))

    def test_a_cancelled_dependency_still_counts_as_settled(self):
        """Abandoned work must not block its dependents forever."""
        self.make_ticket("TEST-001", status="cancelled")
        self.make_ticket("TEST-002", depends_on=["TEST-001"])
        loaded = next_task.load_all_tickets(self.store)

        self.assertFalse(next_task.is_blocked(loaded["TEST-002"], loaded))

    def test_the_dashboard_shows_it_as_blocked_rather_than_ready(self):
        import dashboard
        self.orphan()

        view = dashboard.build_store_view(self.store)

        self.assertEqual([t["id"] for t in view["blocked"]], ["TEST-002"])
        self.assertEqual(view["ready"], [])

    def test_finishing_a_ticket_does_not_claim_to_unlock_one_still_missing_a_dependency(self):
        import dashboard
        self.make_ticket("TEST-001")
        self.make_ticket("TEST-002")
        self.make_ticket("TEST-003", depends_on=["TEST-001", "TEST-002"])
        (self.tickets_dir / "TEST-002.json").unlink()
        loaded = next_task.load_all_tickets(self.store)

        self.assertEqual(dashboard.would_unlock("TEST-001", loaded), [])


class ExampleTicketTest(StoreTestCase):
    """The committed example is the only ticket anyone reads without running the
    tool, so it has to stay honest about the real shape."""

    def load_example(self):
        path = REPO_ROOT / "docs" / "example-ticket.json"
        self.assertTrue(path.is_file(), f"missing {path}")
        return next_task.read_ticket(path)

    def test_example_has_exactly_the_fields_a_real_ticket_has(self):
        self.run_cli("create", "a real one", "--desc", "d", "--tags", "x")
        real = next(iter(next_task.load_all_tickets(self.store).values()))

        self.assertEqual(sorted(self.load_example()), sorted(real))

    def test_example_uses_valid_values(self):
        example = self.load_example()

        self.assertIn(example["status"], next_task.STATUSES)
        self.assertIn(example["priority"], next_task.PRIORITIES)
        self.assertIsInstance(example["tags"], list)
        self.assertIsInstance(example["depends_on"], list)

    def test_example_is_not_discoverable_as_a_store(self):
        """docs/ must not become a third store on the dashboard."""
        import dashboard

        names = [p.name for p in dashboard.discover_stores(REPO_ROOT)]

        self.assertNotIn("docs", names)


if __name__ == "__main__":
    unittest.main()
