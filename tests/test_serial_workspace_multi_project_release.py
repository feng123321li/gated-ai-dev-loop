from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hdg import planning


class _RepositoryDouble:
    def __init__(self, root: Path, requests: list[dict]) -> None:
        self.root = root
        self.requests = requests
        self.released_evidence: dict | None = None

    @staticmethod
    def workspace_turn_release(_root_id: str) -> None:
        return None

    @staticmethod
    def unexpired_cancelled_receiver_leases(
        _root_id: str,
    ) -> list[dict]:
        return []

    @staticmethod
    def serial_workspace_release_blockers(_root_id: str) -> dict:
        return {"receiverClaims": [], "dispatchReservations": []}

    @staticmethod
    def hierarchy(_root_id: str) -> dict:
        return {"hierarchy": {"delivery": {}}}

    def workspace_turn_start(self, _root_id: str) -> dict:
        return {
            "projects": [
                {
                    "projectId": request["projectId"],
                    "turnStartCommit": f"start-{request['projectId']}",
                }
                for request in self.requests
            ]
        }

    def release_serial_workspace_turn(
        self,
        _root_id: str,
        *,
        evidence: dict,
    ) -> None:
        self.released_evidence = evidence


def _fixture(root: Path) -> tuple[list[Path], list[dict], _RepositoryDouble]:
    project_roots = [root / "project-a", root / "project-b"]
    for project_root in project_roots:
        project_root.mkdir()
    requests = [
        {
            "projectId": f"project-{index}",
            "repositoryRoot": str(project_root),
            "coordinatorWorkspace": index == 1,
            "gitBinding": {"branchRef": f"feature/project-{index}"},
        }
        for index, project_root in enumerate(project_roots, start=1)
    ]
    return project_roots, requests, _RepositoryDouble(root, requests)


class SerialWorkspaceMultiProjectReleaseTests(unittest.TestCase):
    def test_release_is_persisted_once_after_all_projects_pass(self) -> None:
        with TemporaryDirectory() as temporary:
            project_roots, requests, repository = _fixture(Path(temporary))

            def commit_range(
                _workspace_root: str,
                start_commit: str,
                head_commit: str,
            ) -> dict:
                project_id = start_commit.removeprefix("start-")
                return {
                    "turnStartCommit": start_commit,
                    "headCommit": head_commit,
                    "turnStartCommitIsAncestor": True,
                    "businessChangedFiles": [
                        {
                            "path": f"{project_id}.txt",
                            "status": "ADDED",
                            "statusCode": "A",
                        }
                    ],
                    "businessTreeFingerprint": f"tree-{project_id}",
                }

            with (
                patch(
                    "hdg.planning_workspace._automatic_workspace_requests",
                    return_value=requests,
                ),
                patch(
                    "hdg.planning_workspace.verify_delivery_git_binding",
                    side_effect=[
                        {"headCommit": "head-project-1"},
                        {"headCommit": "head-project-2"},
                    ],
                ),
                patch(
                    "hdg.planning_workspace.inspect_delivery_git_workspace",
                    return_value={
                        "workingTree": {
                            "clean": True,
                            "stateFingerprint": "clean-fingerprint",
                        }
                    },
                ),
                patch(
                    "hdg.planning_workspace.inspect_business_commit_range",
                    side_effect=commit_range,
                ),
            ):
                barrier = planning._serial_commit_barrier(
                    repository,
                    str(project_roots[0]),
                    {"rootId": "d-multi", "status": "COMPLETED"},
                )

            self.assertIsNone(barrier)
            self.assertEqual(
                [
                    project["projectId"]
                    for project in repository.released_evidence["projects"]
                ],
                ["project-1", "project-2"],
            )

    def test_partial_project_success_does_not_persist_release(self) -> None:
        with TemporaryDirectory() as temporary:
            project_roots, requests, repository = _fixture(Path(temporary))
            workspace_results = iter(
                [
                    {
                        "workingTree": {
                            "clean": True,
                            "stateFingerprint": "clean-fingerprint",
                        }
                    },
                    {
                        "workingTree": {
                            "clean": False,
                            "stateFingerprint": "dirty-fingerprint",
                        }
                    },
                ]
            )
            with (
                patch(
                    "hdg.planning_workspace._automatic_workspace_requests",
                    return_value=requests,
                ),
                patch(
                    "hdg.planning_workspace.verify_delivery_git_binding",
                    side_effect=[
                        {"headCommit": "head-project-1"},
                        {"headCommit": "head-project-2"},
                    ],
                ),
                patch(
                    "hdg.planning_workspace.inspect_delivery_git_workspace",
                    side_effect=lambda _root: next(workspace_results),
                ),
                patch(
                    "hdg.planning_workspace.inspect_business_commit_range",
                    return_value={
                        "turnStartCommit": "start-project-1",
                        "headCommit": "head-project-1",
                        "turnStartCommitIsAncestor": True,
                        "businessChangedFiles": [
                            {
                                "path": "project-1.txt",
                                "status": "ADDED",
                                "statusCode": "A",
                            }
                        ],
                        "businessTreeFingerprint": "tree-project-1",
                    },
                ),
            ):
                barrier = planning._serial_commit_barrier(
                    repository,
                    str(project_roots[0]),
                    {"rootId": "d-multi", "status": "COMPLETED"},
                )

            self.assertEqual(barrier["state"], "WAITING_FOR_WORKSPACE_COMMIT")
            self.assertEqual(
                barrier["projectBarriers"][0]["projectId"],
                "project-2",
            )
            self.assertEqual(
                barrier["projectBarriers"][0]["reason"],
                "UNCOMMITTED_CHANGES",
            )
            self.assertIsNone(repository.released_evidence)


if __name__ == "__main__":
    unittest.main()
