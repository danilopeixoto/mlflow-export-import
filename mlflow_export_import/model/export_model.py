"""
Export a registered model and all the experiment runs associated with each version.
"""

import json
import os
import click
import mlflow
from mlflow.utils.proto_json_utils import message_to_json

from mlflow_export_import.common import MlflowExportImportException
from mlflow_export_import.common import filesystem as _filesystem
from mlflow_export_import.run.export_run import RunExporter
from mlflow_export_import import utils, click_doc

class ModelExporter():

    def __init__(self,  mlflow_client, export_source_tags=False, notebook_formats=None, stages=None, versions=None, export_run=True):
        """
        :param mlflow_client: MLflow client or if None create default client.
        :param export_source_tags: Export source run metadata tags.
        :param notebook_formats: List of notebook formats to export. Values are SOURCE, HTML, JUPYTER or DBC.
        :param stages: Stages to export. Default is all stages. Values are Production, Staging, Archived and None.
        :param export_run: Export the run that generated a registered model's version.
        """
        self.mlflow_client = mlflow_client
        self.run_exporter = RunExporter(self.mlflow_client, export_source_tags=export_source_tags, notebook_formats=notebook_formats)
        self.stages = self._normalize_stages(stages)
        self.export_run = export_run
        self.versions = versions if versions else []
        if len(self.stages) > 0 and len(self.versions) > 0:
            raise MlflowExportImportException(f"Both stages {self.stages} and versions {self.versions} cannot be set")
        self.export_run = export_run


    def export_model(self, model_name, output_dir):
        """
        :param model_name: Registered model name.
        :param output_dir: Output directory.
        :return: Returns bool if export succeeded and the model name.
        """
        try:
            self._export_model(model_name, output_dir)
            return True, model_name
        except Exception as e:
            print("ERROR:", e)
            return False, model_name


    def _export_model(self, model_name, output_dir):
        fs = _filesystem.get_filesystem(output_dir)

        fs.mkdirs(output_dir)
        output_versions = []
        versions = self.mlflow_client.search_model_versions(f"name='{model_name}'")
        print(f"Found {len(versions)} versions for model '{model_name}'")
        manifest = []
        exported_versions = 0
        for vr in versions:
            if len(self.stages) > 0 and not vr.current_stage.lower() in self.stages:
                continue
            if len(self.versions) > 0 and not vr.version in self.versions:
                continue
            run_id = vr.run_id
            opath = os.path.join(output_dir,run_id)
            opath = opath.replace("dbfs:", "/dbfs")
            dct = { "version": vr.version, "stage": vr.current_stage, "run_id": run_id, "description": vr.description, "tags": vr.tags }
            print(f"Exporting: {dct}")
            manifest.append(dct)
            try:
                if self.export_run:
                    self.run_exporter.export_run(run_id, opath)
                run = self.mlflow_client.get_run(run_id)
                dct = dict(vr)
                dct["_run_artifact_uri"] = run.info.artifact_uri
                experiment = mlflow.get_experiment(run.info.experiment_id)
                dct["_experiment_name"] = experiment.name
                output_versions.append(dct)
                exported_versions += 1
            except mlflow.exceptions.RestException as e:
                if "RESOURCE_DOES_NOT_EXIST: Run" in str(e):
                    print(f"WARNING: Run for version {vr.version} does not exist. {e}")
                else:
                    import traceback
                    traceback.print_exc()
        output_versions.sort(key=lambda x: x["version"], reverse=False)

        model_obj = self.mlflow_client.get_registered_model(model_name)
        model_proto = model_obj.to_proto()
        model = json.loads(message_to_json(model_proto))

        export_info = { "export_info":
            { **utils.create_export_info(),
              **{ "num_target_stages": len(self.stages),
                  "num_target_versions": len(self.versions),
                  "num_src_versions": len(versions),
                  "num_dst_versions": len(output_versions)
                }
            }
        }
        model = {'registered_model': model, **export_info }
        model["registered_model"]["latest_versions"] = output_versions

        print(f"Exported {exported_versions}/{len(output_versions)} versions for model '{model_name}'")
        path = os.path.join(output_dir, "model.json")
        utils.write_json_file(fs, path, model)
        return manifest


    def _normalize_stages(self, stages):
        from mlflow.entities.model_registry import model_version_stages
        if stages is None:
            return []
        if isinstance(stages, str):
            stages = stages.split(",")
        stages = [stage.lower() for stage in stages]
        for stage in stages:
            if stage not in model_version_stages._CANONICAL_MAPPING:
                print(f"WARNING: stage '{stage}' must be one of: {model_version_stages.ALL_STAGES}")
        return stages


@click.command()
@click.option("--model",
    help="Registered model name.",
    type=str,
    required=True
)
@click.option("--output-dir",
    help="Output directory.",
    type=str,
    required=True
)
@click.option("--export-source-tags",
    help=click_doc.export_source_tags,
    type=bool,
    default=False,
    show_default=True
)
@click.option("--notebook-formats",
    help=click_doc.notebook_formats,
    type=str,
    default="",
    show_default=True
)
@click.option("--stages",
    help=f"{click_doc.model_stages} Mututally exclusive with option --versions." ,
    type=str,
    required=False
)
@click.option("--versions",
    help="Export specified versions (comma separated). Mututally exclusive with option --stages.",
    type=str,
    required=False
)
def main(model, output_dir, export_source_tags, notebook_formats, stages, versions):
    print("Options:")
    for k,v in locals().items():
        print(f"  {k}: {v}")
    mlflow_client = mlflow.client.MlflowClient()
    versions = versions.split(",") if versions else []
    exporter = ModelExporter(mlflow_client, export_source_tags=export_source_tags, notebook_formats=utils.string_to_list(notebook_formats), stages=stages, versions=versions)
    exporter.export_model(model, output_dir)


if __name__ == "__main__":
    main()
