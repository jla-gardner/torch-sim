import copy

import pytest
import torch

from torchsim.state import BaseState, concatenate_states


# skip tests if we don't have torch scatter
try:
    from torchsim.optimizers import fire

except ImportError:
    pytest.skip(
        "torch_scatter not installed, skipping batched optimizer tests",
        allow_module_level=True,
    )


def test_fire_optimization(
    si_base_state: BaseState, lj_calculator: torch.nn.Module
) -> None:
    """Test that the FIRE optimizer actually minimizes energy."""

    # Add some random displacement to positions
    perturbed_positions = (
        si_base_state.positions + torch.randn_like(si_base_state.positions) * 0.1
    )

    si_base_state.positions = perturbed_positions
    initial_state = si_base_state

    # Initialize FIRE optimizer
    state, update_fn = fire(
        state=initial_state,
        model=lj_calculator,
        dt_max=0.3,
        dt_start=0.1,
    )

    # Run optimization for a few steps
    energies = [1000, state.energy.item()]
    while abs(energies[-2] - energies[-1]) > 1e-6:
        state = update_fn(state)
        energies.append(state.energy.item())

    energies = energies[1:]

    # Check that energy decreased
    assert energies[-1] < energies[0], (
        f"FIRE optimization should reduce energy "
        f"(initial: {energies[0]}, final: {energies[-1]})"
    )

    # Check force convergence
    max_force = torch.max(torch.norm(state.forces, dim=1))
    assert max_force < 0.2, f"Forces should be small after optimization (got {max_force})"

    assert not torch.allclose(state.positions, initial_state.positions)
    assert not torch.allclose(state.cell, initial_state.cell)


def test_fire_multi_batch(
    si_base_state: BaseState, lj_calculator: torch.nn.Module
) -> None:
    """Test FIRE optimization with multiple batches."""
    # Create a multi-batch system by duplicating ar_fcc_state

    generator = torch.Generator(device=si_base_state.device)

    si_base_state_1 = copy.deepcopy(si_base_state)
    si_base_state_2 = copy.deepcopy(si_base_state)

    for state in [si_base_state_1, si_base_state_2]:
        generator.manual_seed(43)
        state.positions += (
            torch.randn(
                state.positions.shape,
                device=state.device,
                generator=generator,
            )
            * 0.05
        )

    multi_state = concatenate_states(
        [si_base_state_1, si_base_state_2],
        device=si_base_state.device,
    )

    # Initialize FIRE optimizer
    state, update_fn = fire(
        state=multi_state,
        model=lj_calculator,
        dt_max=0.3,
        dt_start=0.1,
    )
    initial_state = copy.deepcopy(state)

    # Run optimization for a few steps
    prev_energy = torch.ones(2, device=state.device, dtype=state.energy.dtype) * 1000
    current_energy = initial_state.energy
    i = 0
    while not torch.allclose(current_energy, prev_energy, atol=1e-9):
        prev_energy = current_energy
        state = update_fn(state)
        current_energy = state.energy

        i += 1
        if i > 500:
            raise ValueError("Optimization did not converge")

    # check that we actually optimized
    assert i > 10

    # Check that energy decreased for both batches
    assert torch.all(state.energy < initial_state.energy), (
        "FIRE optimization should reduce energy for all batches"
    )

    # transfer the energy and force checks to the batched optimizer
    max_force = torch.max(torch.norm(state.forces, dim=1))
    assert torch.all(max_force < 0.1), (
        f"Forces should be small after optimization (got {max_force})"
    )

    n_ar_atoms = si_base_state.n_atoms
    assert not torch.allclose(
        state.positions[:n_ar_atoms], multi_state.positions[:n_ar_atoms]
    )
    assert not torch.allclose(
        state.positions[n_ar_atoms:], multi_state.positions[n_ar_atoms:]
    )
    assert not torch.allclose(state.cell, multi_state.cell)

    # we are evolving identical sysmems
    assert current_energy[0] == current_energy[1]


def test_fire_batch_consistency(
    si_base_state: BaseState, lj_calculator: torch.nn.Module
) -> None:
    """Test batched FIRE optimization is consistent with individual optimizations."""
    generator = torch.Generator(device=si_base_state.device)

    si_base_state_1 = copy.deepcopy(si_base_state)
    si_base_state_2 = copy.deepcopy(si_base_state)

    # Add same random perturbation to both states
    for state in [si_base_state_1, si_base_state_2]:
        generator.manual_seed(43)
        state.positions += (
            torch.randn(
                state.positions.shape,
                device=state.device,
                generator=generator,
            )
            * 0.05
        )

    # Optimize each state individually
    final_individual_states = []
    total_steps = []

    def energy_converged(current_energy: float, prev_energy: float) -> bool:
        """Check if optimization should continue based on energy convergence."""
        return not torch.allclose(current_energy, prev_energy, atol=1e-6)

    for state in [si_base_state_1, si_base_state_2]:
        state_opt, update_fn = fire(
            state=copy.deepcopy(state),
            model=lj_calculator,
            dt_max=0.3,
            dt_start=0.1,
        )

        # Run optimization until convergence
        current_energy = state_opt.energy
        prev_energy = current_energy + 1

        i = 0
        while energy_converged(current_energy, prev_energy):
            prev_energy = current_energy
            state_opt = update_fn(state_opt)
            current_energy = state_opt.energy
            i += 1
            if i > 1000:
                raise ValueError("Optimization did not converge")

        final_individual_states.append(state_opt)
        total_steps.append(i)

    # Now optimize both states together in a batch
    multi_state = concatenate_states(
        [copy.deepcopy(si_base_state_1), copy.deepcopy(si_base_state_2)],
        device=si_base_state.device,
    )

    batch_state, batch_update_fn = fire(
        state=copy.deepcopy(multi_state),
        model=lj_calculator,
        dt_max=0.3,
        dt_start=0.1,
    )

    # Run optimization until convergence for both batches
    current_energies = batch_state.energy.clone()
    prev_energies = current_energies + 1

    i = 0
    while energy_converged(current_energies[0], prev_energies[0]) and energy_converged(
        current_energies[1], prev_energies[1]
    ):
        prev_energies = current_energies.clone()
        batch_state = batch_update_fn(batch_state)
        current_energies = batch_state.energy.clone()
        i += 1
        if i > 1000:
            raise ValueError("Optimization did not converge")

    individual_energies = [state.energy.item() for state in final_individual_states]
    # Check that final energies from batched optimization match individual optimizations
    for i, individual_energy in enumerate(individual_energies):
        assert abs(batch_state.energy[i].item() - individual_energy) < 1e-4, (
            f"Energy for batch {i} doesn't match individual optimization: "
            f"batch={batch_state.energy[i].item()}, individual={individual_energy}"
        )
