# -*- coding: utf-8 -*-
# Licensed under a 3-clause BSD style license - see LICENSE.rst

from __future__ import (absolute_import, division, print_function,
                        unicode_literals)

import six

import logging

from . import Command
from ..benchmarks import Benchmarks
from ..console import log
from ..machine import Machine
from ..repo import get_repo
from ..results import (Results, find_latest_result_hash, get_existing_hashes,
                       iter_results_for_machine_and_hash)
from .. import util

from .setup import Setup

from . import common_args


def _do_build(args):
    env, conf, commit_hash = args
    try:
        with log.set_level(logging.WARN):
            env.install_project(conf, commit_hash)
    except util.ProcessError:
        return False
    return True


def _do_build_multiprocess(args):
    """
    multiprocessing callback to build the project in one particular
    environment.
    """
    try:
        return _do_build(args)
    except:
        import traceback
        traceback.format_exc()
        raise


class Run(Command):
    @classmethod
    def setup_arguments(cls, subparsers):
        parser = subparsers.add_parser(
            "run", help="Run a benchmark suite",
            description="Run a benchmark suite.")

        parser.add_argument(
            'range', nargs='?', default=None,
            help="""Range of commits to benchmark.  For a git
            repository, this is passed as the first argument to ``git
            log``.  See 'specifying ranges' section of the
            `gitrevisions` manpage for more info.  Also accepts the
            special values 'NEW', 'ALL', and 'EXISTING'. 'NEW' will
            benchmark all commits since the latest benchmarked on this
            machine.  'ALL' will benchmark all commits in the project.
            'EXISTING' will benchmark against all commits for which
            there are existing benchmarks on any machine. By default,
            will benchmark the head of the current master branch.""")
        parser.add_argument(
            "--steps", "-s", type=int, default=0,
            help="""Maximum number of steps to benchmark.  This is
            used to subsample the commits determined by range to a
            reasonable number.""")
        common_args.add_bench(parser)
        parser.add_argument(
            "--profile", "-p", action="store_true",
            help="""In addition to timing, run the benchmarks through
            the `cProfile` profiler and store the results.""")
        common_args.add_parallel(parser)
        common_args.add_show_stderr(parser)
        parser.add_argument(
            "--quick", "-q", action="store_true",
            help="""Do a "quick" run, where each benchmark function is
            run only once.  This is useful to find basic errors in the
            benchmark functions faster.  The results are unlikely to
            be useful, and thus are not saved.""")
        common_args.add_python(parser)
        parser.add_argument(
            "--dry-run", "-n", action="store_true",
            default=None,
            help="""Do not save any results to disk.""")
        common_args.add_machine(parser)
        parser.add_argument(
            "--skip-existing-successful", action="store_true",
            help="""Skip running benchmarks that have previous successful
            results""")
        parser.add_argument(
            "--skip-existing-failed", action="store_true",
            help="""Skip running benchmarks that have previous failed
            results""")
        parser.add_argument(
            "--skip-existing-commits", action="store_true",
            help="""Skip running benchmarks for commits that have existing
            results""")
        parser.add_argument(
            "--skip-existing", "-k", action="store_true",
            help="""Skip running benchmarks that have previous successful
            or failed results""")

        parser.set_defaults(func=cls.run_from_args)

        return parser

    @classmethod
    def run_from_conf_args(cls, conf, args):
        return cls.run(
            conf=conf, range_spec=args.range, steps=args.steps,
            bench=args.bench, parallel=args.parallel,
            show_stderr=args.show_stderr, quick=args.quick,
            profile=args.profile, python=args.python,
            dry_run=args.dry_run, machine=args.machine,
            skip_successful=args.skip_existing_successful or args.skip_existing,
            skip_failed=args.skip_existing_failed or args.skip_existing,
            skip_existing_commits=args.skip_existing_commits
        )

    @classmethod
    def run(cls, conf, range_spec=None, steps=0, bench=None, parallel=1,
            show_stderr=False, quick=False, profile=False, python=None,
            dry_run=False, machine=None, _machine_file=None, skip_successful=False,
            skip_failed=False, skip_existing_commits=False, _returns={}):
        params = {}
        machine_params = Machine.load(
            machine_name=machine,
            _path=_machine_file, interactive=True)
        params.update(machine_params.__dict__)
        machine_params.save(conf.results_dir)

        if python == "same":
            conf.dvcs = "none"
            conf.repo = ""
            dry_run = True

        repo = get_repo(conf)

        if python is not None:
            conf.pythons = [python]
        else:
            repo.pull()

        if range_spec is None:
            commit_hashes = [repo.get_hash_from_master()]
        elif range_spec == 'EXISTING':
            commit_hashes = [h for h, d in get_existing_hashes(
                conf.results_dir)]
        elif range_spec == 'NEW':
            latest_result = find_latest_result_hash(
                machine_params.machine, conf.results_dir)
            commit_hashes = repo.get_hashes_from_range(
                repo.get_new_range_spec(latest_result))
        elif range_spec == "ALL":
            commit_hashes = repo.get_hashes_from_range("")
        elif isinstance(range_spec, list):
            commit_hashes = range_spec
        else:
            commit_hashes = repo.get_hashes_from_range(range_spec)

        if len(commit_hashes) == 0:
            log.error("No commit hashes selected")
            return 1

        if steps > 0:
            spacing = max(float(len(commit_hashes)) / steps, 1)
            spaced = []
            i = 0
            while int(i) < len(commit_hashes) and len(spaced) < steps:
                spaced.append(commit_hashes[int(i)])
                i += spacing
            commit_hashes = spaced

        environments = Setup.run(conf=conf, parallel=parallel)
        if len(environments) == 0:
            log.error("No environments selected")
            return 1
        if range_spec is not None:
            for env in environments:
                if not env.can_install_project():
                    raise util.UserError(
                        "No range spec may be specified if benchmarking in "
                        "an existing environment")

        benchmarks = Benchmarks(conf, regex=bench)
        if len(benchmarks) == 0:
            log.error("No benchmarks selected")
            return 1
        benchmarks.save()

        steps = len(commit_hashes) * len(benchmarks) * len(environments)

        log.info(
            "Running {0} total benchmarks "
            "({1} commits * {2} environments * {3} benchmarks)".format(
                steps, len(commit_hashes),
                len(environments), len(benchmarks)), "green")
        log.set_nitems(steps)

        parallel, multiprocessing = util.get_multiprocessing(parallel)

        _returns['benchmarks'] = benchmarks
        _returns['environments'] = environments
        _returns['machine_params'] = machine_params.__dict__

        for commit_hash in commit_hashes:
            skipped_benchmarks = set()

            if skip_successful or skip_failed or skip_existing_commits:
                try:
                    for result in iter_results_for_machine_and_hash(
                            conf.results_dir, machine_params.machine, commit_hash):

                        if skip_existing_commits:
                            skipped_benchmarks.update(benchmarks)
                            break

                        for key, value in six.iteritems(result.results):
                            failed = value is None or (isinstance(value, dict) and None in value['result'])
                            if skip_failed and failed:
                                skipped_benchmarks.add(key)
                            if skip_successful and not failed:
                                skipped_benchmarks.add(key)
                except IOError:
                    pass

            for env in environments:
                for bench in benchmarks:
                    if bench in skipped_benchmarks:
                        log.step()

            if not set(six.iterkeys(benchmarks)).difference(skipped_benchmarks):
                continue

            if commit_hash:
                log.info(
                    "For {0} commit hash {1}:".format(
                        conf.project, commit_hash[:8]))

            with log.indent():
                for subenv in util.iter_chunks(environments, parallel):
                    log.info("Building for {0}".format(
                        ', '.join([x.name for x in subenv])))
                    with log.indent():
                        args = [(env, conf, commit_hash) for env in subenv]
                        if parallel != 1:
                            pool = multiprocessing.Pool(parallel)
                            successes = pool.map(_do_build_multiprocess, args)
                            pool.close()
                        else:
                            successes = map(_do_build, args)

                    for env, success in zip(subenv, successes):
                        if success:
                            params['python'] = env.python
                            params.update(env.requirements)
                            results = benchmarks.run_benchmarks(
                                env, show_stderr=show_stderr, quick=quick,
                                profile=profile, skip=skipped_benchmarks)
                        else:
                            results = benchmarks.skip_benchmarks(env)

                        if dry_run:
                            continue

                        result = Results(
                            params,
                            env.requirements,
                            commit_hash,
                            repo.get_date(commit_hash),
                            env.python)

                        for benchmark_name, d in six.iteritems(results):
                            result.add_time(benchmark_name, d['result'])
                            if 'profile' in d:
                                result.add_profile(
                                    benchmark_name,
                                    d['profile'])

                        result.update_save(conf.results_dir)
