"""
CLI utility to reset the RECLAIM local world state database.
Run with: python -m reclaim.world.reset
"""

from reclaim.world.engine import WorldStateEngine


def reset_world():
    engine = WorldStateEngine()
    engine.reset_database()
    print("RECLAIM world state database has been completely reset.")


if __name__ == "__main__":
    reset_world()
