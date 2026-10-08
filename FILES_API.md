# File storage and vector search

完整接口文档见 [API.md](API.md)。

`userid` is a string in JSON, filesystem paths, and Milvus records. The demo user is `"1"`. User IDs may contain ASCII letters, digits, dots, underscores, `@`, and hyphens, up to 128 characters. Collection names contain only ASCII letters, digits, and underscores, up to 64 characters. Supported documents are `.txt`, `.md`, `.html`, and `.htm`.

All users share one Milvus physical collection. Each vector record has a string `userid`, `collection_name`, and `doc_id`; search filters by user and logical collection.

## Collections and files

- `GET /api/v1/users/{userid}/collections`: list active collections.
- `POST /api/v1/users/{userid}/collections/{collection_name}`: create or reactivate a collection.
- `DELETE /api/v1/users/{userid}/collections/{collection_name}`: delete its source files, vectors, and index entries, then keep a disabled marker.
- `GET /api/v1/users/{userid}/collections/{collection_name}/files`: list source files.
- `DELETE /api/v1/users/{userid}/collections/{collection_name}/files/{filename}`: delete one source file, its vectors, and its index entry.
- `PUT /api/v1/users/{userid}/collections/{collection_name}/files/{filename}`: upload one raw file and synchronously vectorize it.
- `POST /api/v1/users/{userid}/collections/{collection_name}/files/batch`: multipart form with one or more `files` parts. Up to 20 files, 20 MiB each. Saves the files, embeds all their text chunks in one model call, writes the vectors, and only then returns `201` with `indexed: true` and the chunk count.

Existing filenames are rejected with HTTP 200 and JSON containing `uploaded: false`, `indexed: false`, `code: "FILE_EXISTS"`, `message`, and `existing_files`. A batch containing an existing filename is rejected before any file is saved or indexed. Upload success returns HTTP 201 with `uploaded: true` and `indexed: true`. Uploads that fail during vectorization return an error. If a source was saved before the error, delete it via the file endpoint before retrying. The web page permits selecting multiple files for one collection and waits for this response before reporting success.

Collection changes and file deletion require a matching Bearer token validated by `JAVA_AUTH_VERIFY_URL`, or the demo login cookie for user `"1"`. Upload and question endpoints currently do not validate identity; deploy them only on a trusted network.

## Questions and maintenance

`POST /ask` and `POST /ask_stream` accept JSON such as:

```json
{"userid":"1","collection_names":["project_a","project_b"],"question":"What do the documents say?","top_k":3}
```

The question page selects multiple collections in a dropdown. Retrieval merges their results by score and returns a global top `k`.

`python rag_builder.py` indexes files placed directly in the knowledge directory by another process. `python rag_builder.py --rebuild` clears and recreates the shared physical vector collection and index. Back up data before rebuilding. Existing integer-userid vector data and older index schemas require this rebuild before they can be searched through string user filters.
