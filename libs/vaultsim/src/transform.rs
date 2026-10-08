use rand::Rng;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Direction {
    Up,
    Down,
    North,
    South,
    East,
    West,
}

impl Direction {
    pub fn parse(s: &str) -> Direction {
        match s {
            "up" => Direction::Up,
            "down" => Direction::Down,
            "north" => Direction::North,
            "south" => Direction::South,
            "east" => Direction::East,
            "west" => Direction::West,
            other => panic!("[transform] unknown direction token: {other}"),
        }
    }

    pub fn is_vertical(self) -> bool {
        matches!(self, Direction::Up | Direction::Down)
    }

    pub fn opposite(self) -> Direction {
        match self {
            Direction::Up => Direction::Down,
            Direction::Down => Direction::Up,
            Direction::North => Direction::South,
            Direction::South => Direction::North,
            Direction::East => Direction::West,
            Direction::West => Direction::East,
        }
    }

    /// Unit vector, Minecraft convention: +X=east, +Y=up, +Z=south.
    pub fn vector(self) -> (i32, i32, i32) {
        match self {
            Direction::Up => (0, 1, 0),
            Direction::Down => (0, -1, 0),
            Direction::North => (0, 0, -1),
            Direction::South => (0, 0, 1),
            Direction::East => (1, 0, 0),
            Direction::West => (-1, 0, 0),
        }
    }
}

/// Rotation around the Y axis only, stored as a count of 90-degree clockwise steps (0..4).
/// Self-derived from JigsawTemplate's direction-mapping table (see SPEC.md):
/// CW90 maps NORTH->EAST->SOUTH->WEST->NORTH, which on (x,z) offsets is (x,z) -> (-z,x).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rotation(u8);

impl Rotation {
    pub const NONE: Rotation = Rotation(0);
    pub const CW90: Rotation = Rotation(1);
    pub const CW180: Rotation = Rotation(2);
    pub const CCW90: Rotation = Rotation(3);

    pub fn compose(self, other: Rotation) -> Rotation {
        Rotation((self.0 + other.0) % 4)
    }

    pub fn rotate_xz(self, x: i32, z: i32) -> (i32, i32) {
        let (mut cx, mut cz) = (x, z);
        for _ in 0..self.0 {
            let (nx, nz) = (-cz, cx);
            cx = nx;
            cz = nz;
        }
        (cx, cz)
    }

    pub fn random(rng: &mut impl Rng) -> Rotation {
        Rotation(rng.gen_range(0..4))
    }

    pub fn inverse(self) -> Rotation {
        Rotation((4 - self.0) % 4)
    }
}

/// Ported verbatim from JigsawTemplate.getRotation(Direction, Direction) - only ever called
/// with horizontal (non-vertical-axis) directions by the real algorithm.
pub fn get_rotation_for_directions(from: Direction, to: Direction) -> Rotation {
    use Direction::*;
    match (from, to) {
        (North, North) => Rotation::NONE,
        (North, South) => Rotation::CW180,
        (North, West) => Rotation::CCW90,
        (North, East) => Rotation::CW90,
        (South, North) => Rotation::CW180,
        (South, South) => Rotation::NONE,
        (South, West) => Rotation::CW90,
        (South, East) => Rotation::CCW90,
        (West, North) => Rotation::CW90,
        (West, South) => Rotation::CCW90,
        (West, West) => Rotation::NONE,
        (West, East) => Rotation::CW180,
        (East, North) => Rotation::CCW90,
        (East, South) => Rotation::CW90,
        (East, West) => Rotation::CW180,
        (East, East) => Rotation::NONE,
        _ => panic!("[transform] get_rotation_for_directions called with vertical direction: {from:?} -> {to:?}"),
    }
}

/// Ported from JigsawTemplate.getRotation(JigsawData, JigsawData, RandomSource).
/// `from` = the child's matching connector, `to` = the parent's requesting jigsaw.
pub fn choose_rotation(
    from_facing: Direction,
    from_side: Direction,
    from_rollable: bool,
    to_facing: Direction,
    to_side: Direction,
    to_rollable: bool,
    rng: &mut impl Rng,
) -> Rotation {
    if from_facing.is_vertical() {
        if from_rollable || to_rollable {
            Rotation::random(rng)
        } else {
            get_rotation_for_directions(from_side, to_side)
        }
    } else {
        get_rotation_for_directions(from_facing, to_facing.opposite())
    }
}

/// A rigid transform: apply(p) = rotate(p) + translation. Used to map a node's own local
/// block coordinates into its parent's local coordinate space (or, once composed all the way
/// up via `then`, into the room/root's local space).
#[derive(Clone, Copy, Debug)]
pub struct Transform {
    pub rotation: Rotation,
    pub translation: (i32, i32, i32),
}

impl Transform {
    pub fn identity() -> Transform {
        Transform {
            rotation: Rotation::NONE,
            translation: (0, 0, 0),
        }
    }

    /// Builds the transform for "rotate around `pivot` (in this node's own local coords),
    /// then translate by `offset`" - exactly the TileProcessor.rotate(rotation, target, true)
    /// + TileProcessor.translate(offset) pair applied in JigsawTemplate.computeChildren.
    pub fn for_attachment(rotation: Rotation, pivot: (i32, i32, i32), offset: (i32, i32, i32)) -> Transform {
        let (rx, rz) = rotation.rotate_xz(pivot.0, pivot.2);
        let translation = (
            pivot.0 - rx + offset.0,
            offset.1, // y is untouched by rotation, so pivot.1 - pivot.1 cancels
            pivot.2 - rz + offset.2,
        );
        Transform { rotation, translation }
    }

    pub fn apply(&self, p: (i32, i32, i32)) -> (i32, i32, i32) {
        let (rx, rz) = self.rotation.rotate_xz(p.0, p.2);
        (rx + self.translation.0, p.1 + self.translation.1, rz + self.translation.2)
    }

    /// Inverse transform: `self.inverse().apply(self.apply(p)) == p`.
    pub fn inverse(&self) -> Transform {
        let inv_rotation = self.rotation.inverse();
        let (rx, rz) = inv_rotation.rotate_xz(self.translation.0, self.translation.2);
        Transform {
            rotation: inv_rotation,
            translation: (-rx, -self.translation.1, -rz),
        }
    }

    /// Composes so that `self.then(inner).apply(p) == self.apply(inner.apply(p))` -
    /// i.e. `inner` maps child-local -> this-local, `self` maps this-local -> root-local,
    /// and the result maps child-local -> root-local directly.
    pub fn then(&self, inner: &Transform) -> Transform {
        let combined_rotation = self.rotation.compose(inner.rotation);
        let (rx, rz) = self.rotation.rotate_xz(inner.translation.0, inner.translation.2);
        let new_translation = (
            rx + self.translation.0,
            inner.translation.1 + self.translation.1,
            rz + self.translation.2,
        );
        Transform {
            rotation: combined_rotation,
            translation: new_translation,
        }
    }
}
