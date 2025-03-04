import pytest
import torch
from ase.build import bulk
from fairchem.core import OCPCalculator
from fairchem.core.models.model_registry import model_name_to_local_file

from torchsim.models.fairchem import FairChemModel
from torchsim.models.interface import validate_model_outputs
from torchsim.runners import atoms_to_state
from torchsim.state import BaseState


pytest.importorskip("torch_sparse")


@pytest.fixture(scope="session")
def model_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    tmp_path = tmp_path_factory.mktemp("fairchem_checkpoints")
    return model_name_to_local_file(
        "EquiformerV2-31M-S2EF-OC20-All+MD", local_cache=str(tmp_path)
    )


@pytest.fixture
def si_system(dtype: torch.dtype, device: torch.device) -> BaseState:
    # Create diamond cubic Silicon
    si_dc = bulk("Si", "diamond", a=5.43)

    return atoms_to_state([si_dc], device, dtype)


@pytest.fixture
def fairchem_calculator(model_path: str, device: torch.device) -> FairChemModel:
    cpu = device.type == "cpu"
    return FairChemModel(
        model=model_path,
        cpu=cpu,
        seed=0,
        pbc=True,
    )


@pytest.fixture
def ocp_calculator(model_path: str) -> OCPCalculator:
    return OCPCalculator(checkpoint_path=model_path, cpu=False, seed=0)


def test_fairchem_ocp_consistency(
    fairchem_calculator: FairChemModel,
    ocp_calculator: OCPCalculator,
    device: torch.device,
) -> None:
    # Set up ASE calculator
    si_dc = bulk("Si", "diamond", a=5.43)
    si_dc.calc = ocp_calculator

    si_state = atoms_to_state([si_dc], device, torch.float32)
    # Get FairChem results
    fairchem_results = fairchem_calculator(
        si_state.positions, si_state.cell, si_state.atomic_numbers
    )

    # Get OCP results
    ocp_forces = torch.tensor(
        si_dc.get_forces(),
        device=device,
        dtype=fairchem_results["forces"].dtype,
    )

    # Test consistency with reasonable tolerances
    torch.testing.assert_close(
        fairchem_results["energy"].item(),
        si_dc.get_potential_energy(),
        rtol=1e-2,
        atol=1e-2,
    )
    torch.testing.assert_close(
        fairchem_results["forces"], ocp_forces, rtol=1e-2, atol=1e-2
    )


@pytest.mark.skipif(
    not torch.cuda.is_available(), reason="Batching does not work properly on CPU"
)
def test_validate_model_outputs(
    fairchem_calculator: FairChemModel, device: torch.device
) -> None:
    validate_model_outputs(fairchem_calculator, device, torch.float32)
