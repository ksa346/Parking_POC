"""Import existing YOLO .txt label files into the database."""
from sqlalchemy.orm import Session
from app.yolo_tool.models import YoloAnnotation, YoloClass, YoloImage


def import_yolo_labels(content: str, image_id: int, project_id: int, db: Session) -> list[dict]:
    """Parse a YOLO detection .txt file and persist annotations."""
    image: YoloImage = db.query(YoloImage).filter(YoloImage.id == image_id).first()
    if not image:
        raise ValueError("Image not found")

    classes: list[YoloClass] = (
        db.query(YoloClass)
        .filter(YoloClass.project_id == project_id)
        .order_by(YoloClass.class_index)
        .all()
    )
    index_to_id = {c.class_index: c.id for c in classes}

    # Clear existing annotations for this image
    db.query(YoloAnnotation).filter(YoloAnnotation.image_id == image_id).delete()

    created = []
    for line in content.strip().splitlines():
        parts = line.strip().split()
        if len(parts) < 5:
            continue
        try:
            class_index = int(parts[0])
            coords = [float(p) for p in parts[1:]]
        except ValueError:
            continue

        class_id = index_to_id.get(class_index)
        if class_id is None:
            continue  # Unknown class — skip silently

        if len(coords) == 4:
            # Detection: cx cy w h
            ann = YoloAnnotation(
                image_id=image_id,
                class_id=class_id,
                type="bbox",
                cx=coords[0],
                cy=coords[1],
                bw=coords[2],
                bh=coords[3],
            )
        elif len(coords) >= 6 and len(coords) % 2 == 0:
            # Segmentation polygon
            ann = YoloAnnotation(image_id=image_id, class_id=class_id, type="polygon")
            ann.set_polygon(coords)
        else:
            continue

        db.add(ann)
        db.flush()
        created.append({"id": ann.id, "class_index": class_index, "type": ann.type})

    if image.status == "unannotated" and created:
        image.status = "in_progress"

    db.commit()
    return created
