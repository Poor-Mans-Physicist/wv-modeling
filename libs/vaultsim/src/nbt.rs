use std::collections::HashMap;
use std::io::Read;

use flate2::read::GzDecoder;

#[derive(Debug, Clone)]
pub enum Tag {
    End,
    Byte(i8),
    Short(i16),
    Int(i32),
    Long(i64),
    Float(f32),
    Double(f64),
    ByteArray(Vec<i8>),
    String(String),
    List(Vec<Tag>),
    Compound(HashMap<String, Tag>),
    IntArray(Vec<i32>),
    LongArray(Vec<i64>),
}

impl Tag {
    pub fn as_compound(&self) -> Option<&HashMap<String, Tag>> {
        match self {
            Tag::Compound(m) => Some(m),
            _ => None,
        }
    }

    pub fn as_list(&self) -> Option<&Vec<Tag>> {
        match self {
            Tag::List(l) => Some(l),
            _ => None,
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Tag::String(s) => Some(s),
            _ => None,
        }
    }

    pub fn as_i64(&self) -> Option<i64> {
        match self {
            Tag::Byte(v) => Some(*v as i64),
            Tag::Short(v) => Some(*v as i64),
            Tag::Int(v) => Some(*v as i64),
            Tag::Long(v) => Some(*v),
            _ => None,
        }
    }

    pub fn get<'a>(&'a self, key: &str) -> Option<&'a Tag> {
        self.as_compound().and_then(|m| m.get(key))
    }
}

struct Reader<'a> {
    buf: &'a [u8],
    pos: usize,
}

impl<'a> Reader<'a> {
    fn new(buf: &'a [u8]) -> Self {
        Reader { buf, pos: 0 }
    }

    fn read_bytes(&mut self, n: usize) -> &'a [u8] {
        let slice = &self.buf[self.pos..self.pos + n];
        self.pos += n;
        slice
    }

    fn read_u8(&mut self) -> u8 {
        let b = self.buf[self.pos];
        self.pos += 1;
        b
    }

    fn read_i8(&mut self) -> i8 {
        self.read_u8() as i8
    }

    fn read_u16(&mut self) -> u16 {
        let b = self.read_bytes(2);
        u16::from_be_bytes([b[0], b[1]])
    }

    fn read_i16(&mut self) -> i16 {
        self.read_u16() as i16
    }

    fn read_i32(&mut self) -> i32 {
        let b = self.read_bytes(4);
        i32::from_be_bytes([b[0], b[1], b[2], b[3]])
    }

    fn read_i64(&mut self) -> i64 {
        let b = self.read_bytes(8);
        i64::from_be_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]])
    }

    fn read_f32(&mut self) -> f32 {
        let b = self.read_bytes(4);
        f32::from_be_bytes([b[0], b[1], b[2], b[3]])
    }

    fn read_f64(&mut self) -> f64 {
        let b = self.read_bytes(8);
        f64::from_be_bytes([b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7]])
    }

    fn read_nbt_string(&mut self) -> String {
        let len = self.read_u16() as usize;
        let bytes = self.read_bytes(len);
        match std::str::from_utf8(bytes) {
            Ok(s) => s.to_string(),
            Err(e) => {
                eprintln!(
                    "[nbt] WARNING: fallback lossy utf8 decode triggered ({} bytes, err: {})",
                    len, e
                );
                String::from_utf8_lossy(bytes).into_owned()
            }
        }
    }

    fn read_payload(&mut self, tag_type: u8) -> Tag {
        match tag_type {
            0 => Tag::End,
            1 => Tag::Byte(self.read_i8()),
            2 => Tag::Short(self.read_i16()),
            3 => Tag::Int(self.read_i32()),
            4 => Tag::Long(self.read_i64()),
            5 => Tag::Float(self.read_f32()),
            6 => Tag::Double(self.read_f64()),
            7 => {
                let len = self.read_i32() as usize;
                let mut v = Vec::with_capacity(len);
                for _ in 0..len {
                    v.push(self.read_i8());
                }
                Tag::ByteArray(v)
            }
            8 => Tag::String(self.read_nbt_string()),
            9 => {
                let elem_type = self.read_u8();
                let len = self.read_i32();
                let mut v = Vec::new();
                if len > 0 {
                    for _ in 0..len {
                        v.push(self.read_payload(elem_type));
                    }
                }
                Tag::List(v)
            }
            10 => {
                let mut m = HashMap::new();
                loop {
                    let t = self.read_u8();
                    if t == 0 {
                        break;
                    }
                    let name = self.read_nbt_string();
                    let val = self.read_payload(t);
                    m.insert(name, val);
                }
                Tag::Compound(m)
            }
            11 => {
                let len = self.read_i32() as usize;
                let mut v = Vec::with_capacity(len);
                for _ in 0..len {
                    v.push(self.read_i32());
                }
                Tag::IntArray(v)
            }
            12 => {
                let len = self.read_i32() as usize;
                let mut v = Vec::with_capacity(len);
                for _ in 0..len {
                    v.push(self.read_i64());
                }
                Tag::LongArray(v)
            }
            other => panic!("[nbt] unknown tag type byte: {other} at pos {}", self.pos),
        }
    }
}

/// Decompresses+parses a gzip-compressed NBT blob already in memory and returns the root
/// compound tag's contents (the outer "named root compound" wrapper is unwrapped, matching
/// nbt_reader.py's `load()`). Takes raw bytes rather than a path so callers can source them
/// from anywhere - a real file (native) or an embedded asset map (wasm) - via `AssetSource`.
pub fn parse_gz(data: &[u8]) -> std::io::Result<Tag> {
    let mut decoder = GzDecoder::new(data);
    let mut decompressed = Vec::new();
    decoder.read_to_end(&mut decompressed)?;

    let mut reader = Reader::new(&decompressed);
    let root_type = reader.read_u8();
    let _root_name = reader.read_nbt_string();
    let root = reader.read_payload(root_type);
    Ok(root)
}
